const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const acorn=require('acorn');
const root=__dirname;
let code=fs.readFileSync(path.join(root,'recovered-dist/assets/App-kNA9sRUP.js'),'utf8');
const patch=path.join(root,'context-activity-patches.cjs');
function editFunction(text,name,edit){const node=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name);return text.slice(0,node.start)+edit(text.slice(node.start,node.end))+text.slice(node.end)}
function replaceOnce(text,from,to){assert.equal(text.split(from).length,2,from);return text.replace(from,to)}
if(fs.existsSync(patch))code=require(patch)(code,{editFunction,replaceOnce});
const tree=acorn.parse(code,{ecmaVersion:'latest',sourceType:'module'});
function source(name){const node=tree.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name)||tree.body.flatMap(n=>n.type==='VariableDeclaration'?n.declarations:[]).find(n=>n.id.name===name);assert.ok(node,name);return node.type==='VariableDeclarator'?'const '+code.slice(node.start,node.end):code.slice(node.start,node.end)}
function evaluate(name,extra={}){return vm.runInNewContext(source(name)+';'+name,{an:[],...extra})}
const event=(id,kind,content,metadata={})=>({id,kind,content,metadata,conversationId:'chat',role:kind.startsWith('tool')?'tool':kind==='message'&&id===1?'user':'assistant',timestamp:'2026-10-31T00:00:'+String(id).padStart(2,'0')+'Z',source:'OpenCore',title:kind});
function walk(node,visit){if(!node||typeof node!=='object')return;visit(node);for(const x of Object.values(node))if(Array.isArray(x))x.forEach(n=>walk(n,visit));else if(x&&typeof x==='object')walk(x,visit)}
function composerEntries(bindings){const fn=source('Fu');let memo;walk(acorn.parse(fn,{ecmaVersion:'latest'}),n=>{if(n.type==='VariableDeclarator'&&n.id.name==='Rn')memo=n.init.arguments[0]});assert.ok(memo);const helpers=Object.fromEntries(['Ll','JI','FI','EI','YI','zv'].map(name=>[name,evaluate(name)]));helpers.EI=evaluate('EI',helpers);return vm.runInNewContext('('+fn.slice(memo.start,memo.end)+')()',{...helpers,...bindings});}
test('an older poll cannot remove an already visible tool call or its output',()=>{
 const merge=evaluate('zv');const user=event(1,'message','Find the game'),call=event(2,'tool_call','{"id":"tool-1","name":"find"}',{id:'tool-1'}),result=event(3,'tool_result','found game.html',{toolCallId:'tool-1'});
 const actual=merge([user,call,result],[user]);assert.deepEqual(Array.from(actual,x=>x.id),[1,2,3]);
});
test('the chat combines newer parent history with a lagging composer poll',()=>{
 const user=event(1,'message','Find the game'),call=event(2,'tool_call','{"id":"tool-1","name":"find"}',{id:'tool-1'}),thought=event(-2,'thinking','Checking the result',{live:true,itemId:'reason-2'});
 const actual=composerEntries({l:[user,call,thought],ye:[user],de:[],dn:{}});assert.ok(actual.some(x=>x.id===2),'tool call disappeared while a newer reasoning preview arrived');
});
test('model-emitted review reasoning remains in the activity timeline',()=>{
 const actual=evaluate('YI')([event(2,'thinking','Checking the written file',{phase:'reviewing'})],[]);assert.equal(actual.length,1);
});
test('every ECHO model shows its reported prompt occupancy, not an empty bar',()=>{
 const telemetry={available:true,active:true,promptTokens:12400,liveTokens:11000,windowTokens:32768,contextMode:'persistent_echo'};
 const jsx=(type,props)=>({type,...props});const meter=evaluate('g8',{p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:n=>String(n),Kp:()=>true});
 const rendered=meter({conversationId:'chat',profile:'frognano-4b',runtime:{status:'running',profile:'frognano-4b',contextSize:32768}});let bar;walk(rendered,x=>{if(x.type==='progress')bar=x});assert.ok(bar);assert.equal(bar.value,12400);assert.equal(bar.max,32768);
});
test('a checkpoint keeps an unsaved streaming item until its persisted copy arrives',async()=>{
 const fn=source('w8');let listener;walk(acorn.parse(fn,{ecmaVersion:'latest'}),n=>{if(n.type==='CallExpression'&&n.callee.name==='It'&&n.arguments[0]?.value==='opencore-generation')listener=n.arguments[1]});assert.ok(listener);
 const user=event(1,'message','Find the game');let saved=[user],preview={conversationId:'chat',runId:'r1',segments:[{kind:'thinking',itemId:'reason-1',content:'I will inspect the game files.'}],phase:'reasoning'};
 const run=vm.runInNewContext('('+fn.slice(listener.start,listener.end)+')',{L:false,ve:0,Tt:{current:'chat'},Bn:async()=>[user],Q:x=>{saved=typeof x==='function'?x(saved):x},G:x=>{preview=typeof x==='function'?x(preview):x},$:()=>{},zv:evaluate('zv'),$mergeGenerationPreview:code.includes('function $mergeGenerationPreview(')?evaluate('$mergeGenerationPreview'):undefined});
 run({payload:{conversationId:'chat',runId:'r1',checkpoint:true,segments:[],content:'',reasoning:'',phase:'tool'}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(preview.segments.length,1,'checkpoint cleared reasoning before the saved record arrived');
 run({payload:{conversationId:'chat',runId:'r1',segments:[{kind:'thinking',itemId:'reason-2',content:'Now I can check the result.'}],phase:'reasoning'}});
 assert.equal(preview.segments.length,2,'the next live update removed the earlier item');
});
test('a persisted checkpoint replaces its preview without duplicating the next pass',()=>{
 const merge=evaluate('$mergeGenerationPreview');
 const current={conversationId:'chat',runId:'r',segments:[{kind:'thinking',content:'Checking file one',itemId:'one'}]};
 const saved=[event(2,'thinking','Checking file one',{itemId:'one'})];
 const checkpoint=merge(current,{conversationId:'chat',runId:'r',checkpoint:true,segments:[]},saved);
 assert.equal(checkpoint.segments.length,0);
 const next=merge(checkpoint,{conversationId:'chat',runId:'r',segments:[{kind:'thinking',content:'Checking file two',itemId:'two'}]});
 assert.equal(next.segments.length,1);assert.equal(next.segments[0].itemId,'two');
});
test('live previews cannot leak into another conversation or another generation',()=>{
 const merge=evaluate('$mergeGenerationPreview'),old={conversationId:'chat',runId:'old',segments:[{kind:'text',content:'Old answer'}]};
 for(const incoming of [{conversationId:'other',runId:'old',segments:[]},{conversationId:'chat',runId:'new',segments:[]}])assert.equal(merge(old,incoming).segments.length,0);
});
test('the meter reflects a smaller actual runtime allocation instead of inventing 32K',()=>{
 const telemetry={available:true,active:true,promptTokens:8000,windowTokens:32768,contextMode:'persistent_echo'},jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:n=>String(n),Kp:()=>true});
 let bar;walk(meter({conversationId:'chat',profile:'test',runtime:{status:'running',profile:'test',contextSize:16384}}),x=>{if(x.type==='progress')bar=x});assert.equal(bar.max,16384);assert.equal(bar.value,8000);
});
test('an unloaded model uses its catalog window rather than the stopped runtimes generic limit',()=>{
 const telemetry={available:false,windowTokens:65536},jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{an:[{id:'ornith-1-5-9b-mtp',contextTokens:32768,echoWorkingWindowTokens:32768}],p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:n=>String(n),Kp:()=>true});
 let bar;walk(meter({conversationId:'chat',profile:'ornith-1-5-9b-mtp',runtime:{status:'stopped',profile:'stopped',contextSize:65536}}),x=>{if(x.type==='progress')bar=x});
 assert.equal(bar.max,32768);assert.equal(bar.value,undefined,'Unknown usage must not be shown as zero');
});
