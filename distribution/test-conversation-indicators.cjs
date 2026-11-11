const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),acorn=require('acorn');
function fixture(){
  const helpers={editFunction(text,name,edit){const node=acorn.parse(text,{ecmaVersion:'latest',sourceType:'module'}).body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===name);return text.slice(0,node.start)+edit(text.slice(node.start,node.end))+text.slice(node.end);},replaceOnce(text,from,to){assert.equal(text.split(from).length,2);return text.replace(from,to);}};
  let app=fs.readFileSync(require.resolve('./recovered-dist/assets/App-kNA9sRUP.js'),'utf8');
  const patch=require('./conversation-indicator-patches.cjs');app=patch(app,helpers);
  const ast=acorn.parse(app,{ecmaVersion:'latest',sourceType:'module'});
  const row=ast.body.filter(n=>n.type==='VariableDeclaration').flatMap(n=>n.declarations).find(n=>n.id.name==='Is').init;
  const read=ast.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='Bn');
  const nativeCalls=[],context=vm.createContext({p:{memo:fn=>fn,useState:()=>[0,()=>{}],useEffect:()=>{}},i:{Fragment:'fragment',jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},Ru:()=>'',Op:'pin-off',jp:'pin',Promise});
  const specifier=ast.body.filter(n=>n.type==='ImportDeclaration').flatMap(n=>n.specifiers).find(n=>['$readConversation','Bn'].includes(n.local.name)).imported.name;
  let index=fs.readFileSync(require.resolve('./recovered-dist/assets/index-CD0EH-dA.js'),'utf8');if(patch.patchIndex)index=patch.patchIndex(index,helpers);
  const indexAst=acorn.parse(index,{ecmaVersion:'latest',sourceType:'module'});
  const nativeName=indexAst.body.filter(n=>n.type==='ExportNamedDeclaration').flatMap(n=>n.specifiers).find(n=>n.exported.name===specifier).local.name;
  const nativeNode=indexAst.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name===nativeName)||indexAst.body.filter(n=>n.type==='VariableDeclaration').flatMap(n=>n.declarations).find(n=>n.id.name===nativeName).init;
  const shared=indexAst.body.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='Kn');
  let load=id=>Promise.resolve([{conversationId:id}]);
  const native=vm.createContext({N:()=>true,na:new Map(),p:(command,args)=>{nativeCalls.push(command);return command==='get_conversation'?load(args.id):Promise.resolve(null);},Promise});
  vm.runInContext(index.slice(shared.start,shared.end),native);
  context.$readConversation=vm.runInContext('('+index.slice(nativeNode.start,nativeNode.end)+')',native);
  vm.runInContext(fs.readFileSync(require.resolve('../src/conversation-indicators.js'),'utf8').replace('export function ','function '),context);
  vm.runInContext('globalThis.store=createConversationIndicators(null,()=>true);globalThis.$conversationIndicators=store;',context);
  native.__opencoreConversationIndicators=context.store;
  if(read)vm.runInContext(app.slice(read.start,read.end),context);else context.Bn=context.$readConversation;
  context.rows=vm.runInContext('('+app.slice(row.start,row.end)+')',context);
  context.nativeCalls=nativeCalls;
  context.setNativeRead=fn=>{load=fn;};
  return context;
}
function dot(f,row){return f.rows({items:[{id:'chat',client:'OpenCore',title:'Test',messageCount:2,status:'complete',...row}]}).props.children[0].props.children[0].props.children[0].props;}
test('the shipped sidebar reserves green for live work and labels unread completions',()=>{
  const f=fixture();
  assert.equal(dot(f,{indicatorActive:true})['data-state'],'working');
  assert.equal(dot(f,{})['data-state'],'unread');
  assert.equal(dot(f,{})['aria-label'],'Finished, unread');
  assert.equal(dot(f,{status:'running'})['data-state'],'idle');
});
test('the shipped history reader acknowledges a loaded selected chat',async()=>{
  const f=fixture();f.store.observe([{id:'chat',status:'complete',messageCount:2}],'chat');
  await f.Bn('chat',{fresh:true});
  assert.deepEqual(f.nativeCalls,['get_conversation']);
  assert.equal(dot(f,{})['data-state'],'idle');
});
test('two callers sharing an old native history read cannot acknowledge a newer completion',async()=>{
  const f=fixture();let finish;
  f.setNativeRead(()=>new Promise(resolve=>{finish=resolve;}));
  f.store.observe([{id:'chat',status:'complete',messageCount:2}],'chat');
  const first=f.Bn('chat');
  f.store.observe([{id:'chat',status:'complete',messageCount:4}],'chat');
  const second=f.Bn('chat');
  finish([{conversationId:'chat'},{conversationId:'chat'}]);
  await Promise.all([first,second]);
  assert.deepEqual(f.nativeCalls,['get_conversation']);
  assert.equal(dot(f,{messageCount:4})['data-state'],'unread');
  f.setNativeRead(()=>Promise.resolve(Array.from({length:4},()=>({conversationId:'chat'}))));
  await f.Bn('chat');
  assert.equal(dot(f,{messageCount:4})['data-state'],'idle');
});
