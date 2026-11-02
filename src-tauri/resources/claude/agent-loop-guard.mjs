import { createHash } from 'node:crypto';

const PLAN = /\b(?:let me|i(?:'ll| will| should| need to| am going to))\b/i;
const ACTIONS = [
  ['build', /\b(?:build|create|make|write|generate|construct)\b/i],
  ['inspect', /\b(?:inspect|check|search|find|locate|look|read|examine)\b/i],
  ['verify', /\b(?:verify|test|validate|run)\b/i],
];
const FILLER = new Set(('empty workspace complete polished feature rich single from scratch ' +
  'first next will need should going let build create make write generate construct inspect ' +
  'check search find locate look read examine verify test validate run the this that with ' +
  'for and then into using html file files best one').split(' '));

function planFingerprint(text) {
  if (!PLAN.test(text)) return null;
  const actions=ACTIONS.filter(([,re])=>re.test(text)).map(([name])=>name);
  if(!actions.length)return null;
  const terms=new Set((text.toLowerCase().match(/[a-z0-9_]+/g)||[]).filter(word=>word.length>2&&!FILLER.has(word)));
  return terms.size>=2?{actions,terms}:null;
}
function similar(left,right) {
  return left.actions.some(value=>right.actions.includes(value)) &&
    [...left.terms].filter(value=>right.terms.has(value)).length>=2;
}
function canonical(value) {
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
  return value;
}
function signature(name,input,result) {
  try {
    const text=JSON.stringify([name,canonical(input),canonical(result)]);
    if(!text)return null;
    return createHash('sha256').update(text).digest('hex');
  } catch { return null; }
}
function actionInput(name,input) {
  if(!/(?:^|__)dev$/.test(name)||!input||typeof input!=='object')return input;
  const {explanation,...action}=input;
  return action;
}
function resultDetails(result,failed=false) {
  let value=result;
  // MCP text envelopes and native SDK failures both carry execution evidence.
  for(let depth=0;depth<4;depth++) {
    if(typeof value==='string') {
      try {value=JSON.parse(value);} catch {break;}
    }
    if(!value||typeof value!=='object')break;
    failed ||= value.isError===true||value.is_error===true||Boolean(value.error)
      ||value.ok===false||value.success===false
      ||['exitCode','exit_code'].some(key=>typeof value[key]==='number'&&value[key]!==0);
    if(!Array.isArray(value.content))break;
    const texts=value.content.filter(item=>item?.type==='text'&&typeof item.text==='string');
    if(texts.length!==1||texts.length!==value.content.length)break;
    value=texts[0].text;
  }
  return {value,failed};
}
function workspaceListing(result) {
  try {
    let value=result;
    if(typeof value==='string')value=JSON.parse(value);
    if(Array.isArray(value?.content)){
      const text=value.content.find(item=>item?.type==='text'&&typeof item.text==='string')?.text;
      if(text)value=JSON.parse(text);
    }
    return typeof value?.workspace==='string'&&Array.isArray(value.files);
  } catch {return false;}
}
function readOnly(name,input,result) {
  if(['Read','Grep','Glob','LS'].includes(name))return true;
  return /(?:^|__)dev$/.test(name)&&(['read','search'].includes(input?.action)||
    (['status','list'].includes(input?.action)&&workspaceListing(result)));
}

// One instance is scoped to one user request, not the life of a conversation.
export class AgentLoopGuard {
  constructor(){this.plans=[];this.messageIds=new Set();this.tools=[];this.toolResultIds=new Set();this.blockedActions=new Set();this.toolIssue=null;}
  observeAssistant(event) {
    if(event?.type!=='assistant'||event.parent_tool_use_id)return null;
    const id=event.message?.id||event.uuid;
    if(id&&this.messageIds.has(id))return null;
    if(id)this.messageIds.add(id);
    const blocks=event.message?.content;
    if(!Array.isArray(blocks)||blocks.some(block=>block?.type==='tool_use'))return null;
    const text=blocks.map(block=>block?.type==='text'?block.text:block?.type==='thinking'?block.thinking:'').filter(Boolean).join('\n');
    const plan=planFingerprint(text);
    if(!plan)return null;
    const prior=this.plans.filter(old=>similar(old,plan)).length;
    this.plans.push(plan);this.plans=this.plans.slice(-6);
    if(this.plans.length>=4&&prior>=3)return {kind:'repeated-plan',message:'OpenCore stopped repeated planning without a tool action. Your conversation and previous tool results are preserved.'};
    return null;
  }
  observeToolUse(name,input) {
    const action=signature(name,actionInput(name,input),null);
    return action&&this.blockedActions.has(action)?this.toolIssue:null;
  }
  observeToolResult(name,input,result,failed=false,toolUseId) {
    if(result!==undefined&&toolUseId){
      if(this.toolResultIds.has(toolUseId))return null;
      this.toolResultIds.add(toolUseId);
      if(this.toolResultIds.size>256)this.toolResultIds.delete(this.toolResultIds.values().next().value);
    }
    this.plans=[];
    const details=resultDetails(result,failed);
    if(result===undefined||(!details.failed&&!readOnly(name,input,details.value))){
      this.tools=[];this.blockedActions.clear();this.toolIssue=null;return null;
    }
    const action=signature(name,actionInput(name,input),null);
    const current=signature(name,actionInput(name,input),[details.value,details.failed]);
    if(!current){this.tools=[];return null;}
    // Bound retained evidence, not task duration or the number of distinct actions.
    this.tools.push({signature:current,action,failed:details.failed});this.tools=this.tools.slice(-96);
    for(let width=1;width<=Math.floor(this.tools.length/3);width++){
      if(this.tools.length<width*3)continue;
      const recent=this.tools.slice(-width*3),cycle=recent.slice(0,width);
      if(recent.every((value,index)=>value.signature===cycle[index%width].signature)) {
        this.blockedActions=new Set(cycle.map(value=>value.action));
        this.toolIssue={kind:'repeated-tool',message:cycle.some(value=>value.failed)
          ?'OpenCore stopped repeated failed tool actions with unchanged results. Previous tool results remain in this chat; the task is incomplete.'
          :'OpenCore stopped repeating the same tool with unchanged results. Previous tool results remain in this chat; the task is incomplete.'};
        return this.toolIssue;
      }
    }
    return null;
  }
}

export async function forwardGuardedAgent(agent,guard,write,reportContext) {
  for await(const message of agent){
    // Forward every completed block before stopping, so existing activity never disappears.
    write({kind:'sdk',message});
    const issue=guard.observeAssistant(message);
    if(issue){
      write({kind:'fatal',error:issue.message});
      let timer;
      try { await Promise.race([agent.interrupt(),new Promise(resolve=>{timer=setTimeout(resolve,1500);})]); }
      catch {} finally {clearTimeout(timer);}
      return;
    }
    if((message.type==='system'&&(message.subtype==='init'||message.subtype==='compact_boundary'))
       ||message.type==='assistant'||message.type==='user')await reportContext();
  }
}

export function guardedToolResult(guard,event,write){
  const failed=event.hook_event_name==='PostToolUseFailure';
  const result=failed?{error:event.error||'Tool execution failed',isError:true}:event.tool_response;
  const issue=guard.observeToolResult(event.tool_name,event.tool_input,result,failed,event.tool_use_id);
  if(!issue)return null;
  write({kind:'fatal',error:issue.message});
  return {continue:false,stopReason:issue.message};
}

export function guardedToolUse(guard,event,write){
  const issue=guard.observeToolUse(event.tool_name,event.tool_input);
  if(!issue)return null;
  write({kind:'fatal',error:issue.message});
  return {continue:false,stopReason:issue.message,hookSpecificOutput:{hookEventName:'PreToolUse',
    permissionDecision:'deny',permissionDecisionReason:issue.message}};
}
