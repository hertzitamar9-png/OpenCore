const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const acorn=require('acorn');
const root=__dirname;
test('an empty first-run composer renders its fallback before any live sample exists',()=>{
 const source=fs.readFileSync(path.join(root,'../src/live-footer.js'),'utf8');
 const React={useState:value=>[value,()=>{}],useEffect(){},createElement:(type,props,children)=>({type,props,children})};
 const stats=vm.runInNewContext(source+';LiveOutputStats',{React});
 assert.equal(stats({conversationId:undefined,fallback:'Waiting for token metrics'}).children,'Waiting for token metrics');
});
let code=fs.readFileSync(path.join(root,'recovered-dist/assets/App-kNA9sRUP.js'),'utf8');
const patch=path.join(root,'context-activity-patches.cjs');
function editFunction(text,name,edit){const node=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name);return text.slice(0,node.start)+edit(text.slice(node.start,node.end))+text.slice(node.end)}
function replaceOnce(text,from,to){assert.equal(text.split(from).length,2,from);return text.replace(from,to)}
if(fs.existsSync(patch))code=require(patch)(code,{editFunction,replaceOnce});
const tree=acorn.parse(code,{ecmaVersion:'latest',sourceType:'module'});
function source(name){const node=tree.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name)||tree.body.flatMap(n=>n.type==='VariableDeclaration'?n.declarations:[]).find(n=>n.id.name===name);assert.ok(node,name);return node.type==='VariableDeclarator'?'const '+code.slice(node.start,node.end):code.slice(node.start,node.end)}
const previewHelpers=['tool-progress.js','generation-preview.js'].map(file=>fs.readFileSync(path.join(root,file),'utf8')).join('\n');
function evaluate(name,extra={}){return vm.runInNewContext(previewHelpers+'\n'+source(name)+';'+name,{an:[],...extra})}
function generationListener(bindings){const fn=source('w8');let listener;walk(acorn.parse(fn,{ecmaVersion:'latest'}),node=>{if(node.type==='CallExpression'&&node.callee.name==='It'&&node.arguments[0]?.value==='opencore-generation')listener=node.arguments[1]});assert.ok(listener);const context=vm.createContext(bindings);vm.runInContext(previewHelpers,context);return{run:vm.runInContext('('+fn.slice(listener.start,listener.end)+')',context),context};}
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

test('an unloaded model does not reuse the archives generic 64K capacity as its actual window',()=>{
 const telemetry={available:true,active:false,promptTokens:20510,windowTokens:65536,contextMode:'persistent_echo'};
 const jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{p:{useState:()=>[telemetry,()=>{}],useEffect(){}},i:{jsx,jsxs:jsx},hr:String,Kp:()=>true,fd:()=>({configuredContextTokens:32768})});
 const rendered=meter({conversationId:'chat',profile:'ornith-1-5-9b-mtp',runtime:{status:'stopped',profile:'ornith-1-5-9b-mtp',contextSize:65536}});
 let bar;walk(rendered,x=>{if(x.type==='progress')bar=x});assert.equal(bar.max,32768);assert.equal(bar.value,20510);
});
test('a checkpoint keeps an unsaved streaming item until its persisted copy arrives',async()=>{
 const fn=source('w8');let listener;walk(acorn.parse(fn,{ecmaVersion:'latest'}),n=>{if(n.type==='CallExpression'&&n.callee.name==='It'&&n.arguments[0]?.value==='opencore-generation')listener=n.arguments[1]});assert.ok(listener);
 const user=event(1,'message','Find the game');let saved=[user],preview={conversationId:'chat',runId:'r1',segments:[{kind:'thinking',itemId:'reason-1',content:'I will inspect the game files.'}],phase:'reasoning'};
 const {run}=generationListener({L:false,ve:0,Tt:{current:'chat'},Bn:async()=>[user],Q:x=>{saved=typeof x==='function'?x(saved):x},G:x=>{preview=typeof x==='function'?x(preview):x},$:()=>{},zv:evaluate('zv')});
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

test('terminal output keeps its beginning after more than 64K streams',()=>{
 const update=evaluate('FI'),call=event(2,'tool_call','{"id":"long","name":"exec"}',{id:'long'}),beginning='FIRST_OUTPUT\n'+'x'.repeat(66000);
 let state=update({},{conversationId:'chat',toolCallId:'long',call,status:'running',delta:beginning});
 state=update(state,{conversationId:'chat',toolCallId:'long',status:'completed',delta:'\nLAST_OUTPUT',result:{output:beginning+'\nLAST_OUTPUT',exitCode:0,status:'completed'}});
 assert.equal(state.long.output.length,66025);assert.ok(state.long.output.startsWith('FIRST_OUTPUT\n'));assert.ok(state.long.output.endsWith('\nLAST_OUTPUT'));
});

test('late running updates cannot revert a completed tool result',()=>{
 const update=evaluate('FI');let state=update({},{conversationId:'chat',toolCallId:'one',status:'completed',result:{output:'first\nlast',exitCode:0,status:'completed'}});
 state=update(state,{conversationId:'chat',toolCallId:'one',status:'running',delta:'\nlate chunk'});
 assert.equal(state.one.status,'completed');assert.equal(state.one.result.exitCode,0);
 assert.equal(state.one.output,'first\nlast\nlate chunk');
});

test('a checkpoint result cannot mask newer streamed output or completion',()=>{
 const call=event(2,'tool_call','{"id":"one","name":"exec","arguments":{"cmd":"check"}}',{id:'one'}),saved=event(3,'tool_result','{"output":"first","status":"running"}',{toolCallId:'one'});
 const merge=evaluate('EI',{Ll:evaluate('Ll')});
 const actual=merge([call,saved],{one:{conversationId:'chat',toolCallId:'one',call,title:'exec',status:'completed',output:'first\nlast',result:{output:'first\nlast',exitCode:0,status:'completed'}}});
 assert.equal(actual.filter(item=>item.kind==='tool_result').length,1);
 const result=JSON.parse(actual.find(item=>item.kind==='tool_result').content);assert.equal(result.output,'first\nlast');assert.equal(result.status,'completed');assert.equal(result.exitCode,0);
});

test('a done notification keeps displayed summaries until native persistence catches up',async()=>{
 const fn=source('w8');let listener;walk(acorn.parse(fn,{ecmaVersion:'latest'}),n=>{if(n.type==='CallExpression'&&n.callee.name==='It'&&n.arguments[0]?.value==='opencore-generation')listener=n.arguments[1]});
 const user=event(1,'message','Inspect it');let saved=[user],preview={conversationId:'chat',runId:'r1',segments:[{kind:'thinking',itemId:'summary-1',content:'Checking the returned tool output.',reasoningSource:'summary'}],phase:'reasoning'};
 const {run}=generationListener({L:false,ve:0,Tt:{current:'chat'},Bn:async()=>saved,Q:x=>{saved=typeof x==='function'?x(saved):x},G:x=>{preview=typeof x==='function'?x(preview):x},$:()=>{},zv:evaluate('zv')});
 run({payload:{conversationId:'chat',runId:'r1',done:true}});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(preview?.segments?.[0]?.content,'Checking the returned tool output.');assert.equal(preview.done,true);
 saved.push(event(4,'thinking','Checking the returned tool output.',{itemId:'summary-1',reasoningSource:'summary'}));
 run({payload:{conversationId:'chat',runId:'r1',done:true}});await new Promise(resolve=>setImmediate(resolve));assert.equal(preview,undefined);
});

test('another chats live update cannot keep a background completion cached as live',async()=>{
 let preview,resolveSaved;const {run,context}=generationListener({L:false,ve:0,Tt:{current:'other'},Bn:()=>new Promise(resolve=>{resolveSaved=resolve}),Q:()=>{},G:value=>{preview=typeof value==='function'?value(preview):value},$:()=>{},zv:evaluate('zv')});
 run({payload:{conversationId:'chat',runId:'first',segments:[{kind:'thinking',itemId:'one',content:'Supplied background summary.'}]}});
 run({payload:{conversationId:'other',runId:'second',segments:[{kind:'thinking',itemId:'two',content:'Visible chat summary.'}]}});
 run({payload:{conversationId:'chat',runId:'first',done:true}});
 run({payload:{conversationId:'other',runId:'second',segments:[{kind:'thinking',itemId:'two',content:'Visible chat summary. More detail.'}]}});
 resolveSaved([event(1,'message','Inspect it')]);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(preview.conversationId,'other');assert.equal(preview.segments[0].content,'Visible chat summary. More detail.');
 const completed=context.$generationPreviewFor('chat');assert.equal(completed.done,true);assert.equal(completed.segments[0].content,'Supplied background summary.');
});

test('a background checkpoint reconciles its own cache without replacing the visible chat',async()=>{
 let preview,resolveSaved;const {run,context}=generationListener({L:false,ve:0,Tt:{current:'other'},Bn:()=>new Promise(resolve=>{resolveSaved=resolve}),Q:()=>{},G:value=>{preview=typeof value==='function'?value(preview):value},$:()=>{},zv:evaluate('zv')});
 run({payload:{conversationId:'chat',runId:'first',segments:[{kind:'thinking',itemId:'one',content:'Saved background summary.'}]}});
 run({payload:{conversationId:'other',runId:'second',segments:[{kind:'text',content:'Other answer'}]}});
 run({payload:{conversationId:'chat',runId:'first',checkpoint:true,segments:[]}});
 run({payload:{conversationId:'other',runId:'second',segments:[{kind:'text',content:'Other answer continues.'}]}});
 resolveSaved([event(2,'thinking','Saved background summary.',{itemId:'one'})]);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(context.$generationPreviewFor('chat').segments.length,0);assert.equal(preview.conversationId,'other');assert.equal(preview.segments[0].content,'Other answer continues.');
});

test('a late saved response cannot overwrite a newer run in the same chat cache',async()=>{
 let preview,resolveSaved;const {run,context}=generationListener({L:false,ve:0,Tt:{current:'chat'},Bn:()=>new Promise(resolve=>{resolveSaved=resolve}),Q:()=>{},G:value=>{preview=typeof value==='function'?value(preview):value},$:()=>{},zv:evaluate('zv')});
 run({payload:{conversationId:'chat',runId:'old',segments:[{kind:'text',content:'Old answer'}]}});run({payload:{conversationId:'chat',runId:'old',done:true}});
 run({payload:{conversationId:'chat',runId:'new',segments:[{kind:'thinking',itemId:'new-summary',content:'New run summary.'}]}});
 resolveSaved([event(2,'message','Old answer')]);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(preview.runId,'new');assert.equal(context.$generationPreviewFor('chat').runId,'new');assert.equal(preview.segments[0].content,'New run summary.');
});

test('the first segmented payload does not duplicate its aggregate reasoning field',()=>{
 const merge=evaluate('$mergeGenerationPreview');
 const actual=merge(undefined,{conversationId:'chat',runId:'first',reasoning:'First summary. Second summary.',segments:[{kind:'thinking',content:'First summary.',itemId:'one'},{kind:'thinking',content:'Second summary.',itemId:'two'}]});
 assert.equal(actual.segments.length,2);assert.deepEqual(Array.from(actual.segments,item=>item.itemId),['one','two']);
});

test('standalone streamed tool results remain visible before their call is saved',()=>{
 const merge=evaluate('EI',{Ll:evaluate('Ll')});
 const actual=merge([],{one:{conversationId:'chat',toolCallId:'one',title:'external-tool',status:'completed',result:'Remote tool finished'}});
 assert.equal(actual.length,1);assert.equal(actual[0].title,'external-tool');assert.ok(actual[0].content.includes('Remote tool finished'));
});

test('a completed preview stays after its user message without appearing to stream',()=>{
 const fn=source('w8');let memo;walk(acorn.parse(fn,{ecmaVersion:'latest'}),n=>{if(n.type==='VariableDeclarator'&&n.id.name==='Jt')memo=n.init.arguments[0]});
 const user=event(1,'message','Inspect it');
 const rows=vm.runInNewContext('('+fn.slice(memo.start,memo.end)+')()',{E:{conversationId:'chat',runId:'r1',done:true,segments:[{kind:'thinking',itemId:'one',content:'Supplied summary.'}]},F:[user],y:'chat',mn:1});
 const actual=composerEntries({l:rows,ye:[user],de:[],dn:{}});
 assert.deepEqual(Array.from(actual,item=>item.id),[1,-2]);assert.equal(actual[1].metadata.previewComplete,true);
});

test('the app-level tool listener retains offscreen output for a returning composer',()=>{
 let listener;const context=vm.createContext({It:(_event,callback)=>{listener=callback;return Promise.resolve(()=>{})}});
 vm.runInContext(previewHelpers,context);const call=event(2,'tool_call','{"id":"one","name":"exec"}',{id:'one'});
 listener({payload:{conversationId:'chat',toolCallId:'one',call,title:'exec',status:'running',delta:'first'}});
 listener({payload:{conversationId:'other',toolCallId:'one',title:'other-tool',status:'running',delta:'other output'}});
 listener({payload:{conversationId:'chat',toolCallId:'one',status:'completed',delta:'\nlast',result:{status:'completed',exitCode:0}}});
 assert.equal(context.$toolPreviewState('chat').one.output,'first\nlast');assert.equal(context.$toolPreviewState('chat').one.call.title,call.title);assert.equal(context.$toolPreviewState('other').one.output,'other output');
});

test('a tool with live terminal output remains marked as using a tool',()=>{
 const call=event(2,'tool_call','{"id":"one","name":"exec"}',{id:'one'}),result=event(-1000000,'tool_result','{"output":"first","status":"running"}',{toolCallId:'one',live:true});
 const jsx=(type,props)=>({type,...props}),ToolGroup=()=>{};
 const render=evaluate('TO',{i:{jsx,jsxs:jsx},iO:evaluate('iO'),ZI:()=>[],Yp:raw=>JSON.parse(raw).status,_O:ToolGroup,nO:()=>[]});
 const actual=render({events:[call,result],active:true});let group;walk(actual,node=>{if(node.type===ToolGroup)group=node});
 assert.ok(group);assert.equal(group.active,true);
});

test('provider supplied summary text and summary metadata are rendered verbatim',()=>{
 const jsx=(type,props)=>({type,...props});let content;
 const render=evaluate('uO',{i:{jsx,jsxs:jsx},et:(_key,value)=>[value,()=>{}],p:{useRef:()=>({current:null})},yp:()=>{},mf:value=>value||{}});
 render({entries:[event(2,'thinking','Provider supplied summary.',{reasoningSource:'summary'}),event(3,'thinking','',{summary:'Provider metadata summary.'})],active:false,renderContent:value=>{content=value;return value}});
 assert.equal(content,'Provider supplied summary.\n\nProvider metadata summary.');
});

test('tool action labels never pose as model authored first person reasoning',()=>{
 const jsx=(type,props)=>({type,...props}),Reasoning=()=>{},ToolGroup=()=>{};
 const render=evaluate('TO',{i:{jsx,jsxs:jsx},iO:evaluate('iO'),ZI:()=>[],Yp:()=> 'pending',_O:ToolGroup,uO:Reasoning,nO:()=>[],_n:String,qu:()=>({label:'Ran a command'}),Zv:()=> 'verify-fixture',Jp:()=>false,MO:()=>undefined});
 const actual=render({events:[event(2,'thinking','A supplied summary.'),event(3,'tool_call','{"id":"one","name":"exec"}',{id:'one'})],active:true});let reasoning;walk(actual,node=>{if(node.type===Reasoning)reasoning=node});
 assert.equal(reasoning.nextAction,'Tool action: Ran a command: verify-fixture');
});
test('the meter reflects a smaller actual runtime allocation instead of inventing 32K',()=>{
 const telemetry={available:true,active:true,promptTokens:8000,windowTokens:32768,contextMode:'persistent_echo'},jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:n=>String(n),Kp:()=>true});
 let bar;walk(meter({conversationId:'chat',profile:'test',runtime:{status:'running',profile:'test',contextSize:16384}}),x=>{if(x.type==='progress')bar=x});assert.equal(bar.max,16384);assert.equal(bar.value,8000);
});
test('an unloaded model uses its catalog window rather than the stopped runtimes generic limit',()=>{
 const telemetry={available:false,windowTokens:65536},jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{fd:()=>({configuredContextTokens:32768}),p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:n=>String(n),Kp:()=>true});
 let bar;walk(meter({conversationId:'chat',profile:'ornith-1-5-9b-mtp',runtime:{status:'stopped',profile:'stopped',contextSize:65536}}),x=>{if(x.type==='progress')bar=x});
 assert.equal(bar.max,32768);assert.equal(bar.value,undefined,'Unknown usage must not be shown as zero');
});
test('an unloaded 32K profile does not show a previous 16K session as its current window',()=>{
 const telemetry={available:true,active:false,modelSessionActive:false,windowTokens:16384,modelActiveTokens:16384,modelUsageKind:'live',contextMode:'persistent_echo'},jsx=(type,props)=>({type,...props});
 const meter=evaluate('g8',{fd:()=>({configuredContextTokens:32768}),p:{useState:()=>[telemetry,()=>{}],useEffect:()=>{}},i:{jsx,jsxs:jsx},hr:String,Kp:()=>true});
 let bar;walk(meter({conversationId:'chat',profile:'ornith',runtime:{status:'stopped',profile:'stopped',contextSize:16384}}),x=>{if(x.type==='progress')bar=x});
 assert.equal(bar.max,32768);assert.equal(bar.value,undefined);
});
