// Transport/control test using the real SDK with a deterministic mock provider.
// This is not a model-quality evaluation; the live-app test uses the real model.
import http from 'node:http';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtempSync,existsSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=mkdtempSync(path.join(tmpdir(),'opencore-sdk-test-'));
const marker=path.join(root,'must-not-exist.txt');
let count=0,denied=false,permissionCount=0,streamDeltas=0,contextReports=[];
function event(res,value){res.write(`event: ${value.type}\ndata: ${JSON.stringify(value)}\n\n`);}
const server=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  const body=JSON.parse(raw||'{}');
  if(req.url.includes('count_tokens')){res.writeHead(200,{'content-type':'application/json'});res.end('{"input_tokens":100}');return;}
  if(!req.url.startsWith('/v1/messages')){res.writeHead(404);res.end('{}');return;}
  count++;
  res.writeHead(200,{'content-type':'text/event-stream'});
  event(res,{type:'message_start',message:{id:`msg_${count}`,type:'message',role:'assistant',model:body.model,content:[],stop_reason:null,stop_sequence:null,usage:{input_tokens:100,output_tokens:0}}});
  if(count===1){
    event(res,{type:'content_block_start',index:0,content_block:{type:'tool_use',id:'call_denied',name:'Bash',input:{}}});
    event(res,{type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:JSON.stringify({command:'echo forbidden > must-not-exist.txt',description:'Write permission test marker'})}});
    event(res,{type:'content_block_stop',index:0});event(res,{type:'message_delta',delta:{stop_reason:'tool_use',stop_sequence:null},usage:{output_tokens:30}});
  }else{
    denied=body.messages.some(m=>Array.isArray(m.content)&&m.content.some(p=>p.type==='tool_result'&&p.tool_use_id==='call_denied'&&p.is_error));
    event(res,{type:'content_block_start',index:0,content_block:{type:'text',text:''}});
    for(const text of ['Permission ','was denied.']){event(res,{type:'content_block_delta',index:0,delta:{type:'text_delta',text}});await new Promise(r=>setTimeout(r,60));}
    event(res,{type:'content_block_stop',index:0});event(res,{type:'message_delta',delta:{stop_reason:'end_turn',stop_sequence:null},usage:{output_tokens:4}});
  }
  event(res,{type:'message_stop'});res.end();
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const child=spawn(process.execPath,['src-tauri/resources/claude/runner.mjs'],{stdio:['pipe','pipe','pipe']});
let result,errors='';child.stderr.on('data',b=>errors+=b);
createInterface({input:child.stdout}).on('line',line=>{
  const event=JSON.parse(line);
  if(event.kind==='permission'){permissionCount++;child.stdin.write(JSON.stringify({kind:'reply',id:event.id,value:false})+'\n');}
  if(event.kind==='sdk'&&event.message.type==='result')result=event.message;
  if(event.kind==='sdk'&&event.message.type==='stream_event'&&event.message.event.delta?.text)streamDeltas++;
  if(event.kind==='context')contextReports.push(event.usage);
});
child.stdin.write(JSON.stringify({kind:'start',cwd:root,configDir:path.join(root,'config'),conversationId:'protocol-test',effort:'off',content:'Run the requested marker command.',tools:[],instructions:'Test the given tool.',gateway:`http://127.0.0.1:${server.address().port}`})+'\n');
const timer=setTimeout(()=>child.kill(),30000);
await new Promise(r=>child.on('exit',r));clearTimeout(timer);server.close();
assert.ok(result&&!result.is_error,errors||JSON.stringify(result));
assert.ok(permissionCount>0);assert.ok(denied);assert.ok(!existsSync(marker));assert.ok(streamDeltas>=2);
assert.ok(contextReports.some(usage=>usage.isAutoCompactEnabled===false),
  `ECHO-managed session must disable SDK auto-compaction; got ${JSON.stringify(contextReports)}`);
let cancelChild,requestClosed=false,cancelStarted=0;
const slow=http.createServer(async(req,res)=>{
  for await(const ignored of req){}
  if(!req.url.startsWith('/v1/messages')){res.writeHead(404);res.end('{}');return;}
  res.writeHead(200,{'content-type':'text/event-stream'});res.flushHeaders();
  res.on('close',()=>{requestClosed=true;});
  cancelStarted=Date.now();cancelChild.stdin.write(JSON.stringify({kind:'cancel'})+'\n');
  // Match the desktop host: allow graceful cleanup, then reap the owned tree.
  setTimeout(()=>{
    if(cancelChild.exitCode!==null||cancelChild.signalCode!==null)return;
    if(process.platform==='win32')spawn('taskkill.exe',['/PID',String(cancelChild.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
    else cancelChild.kill('SIGKILL');
  },3000).unref();
});
await new Promise(r=>slow.listen(0,'127.0.0.1',r));
cancelChild=spawn(process.execPath,['src-tauri/resources/claude/runner.mjs'],{stdio:['pipe','pipe','pipe']});
cancelChild.stdout.resume();cancelChild.stderr.resume();
cancelChild.stdin.write(JSON.stringify({kind:'start',cwd:root,configDir:path.join(root,'cancel-config'),conversationId:'cancel-test',effort:'off',content:'Wait for the response.',tools:[],instructions:'Test cancellation.',gateway:`http://127.0.0.1:${slow.address().port}`})+'\n');
const cancelTimer=setTimeout(()=>cancelChild.kill(),10000);
await new Promise(r=>cancelChild.on('exit',r));clearTimeout(cancelTimer);
await new Promise(r=>setTimeout(r,150));slow.closeAllConnections();slow.close();
const cancellationMs=Date.now()-cancelStarted;
console.log(JSON.stringify({cancellationMs,cancelStarted,requestClosed}));
assert.ok(cancelStarted>0&&cancellationMs<5000);assert.ok(requestClosed);
const report={provider:'deterministic mock',sdk:'0.3.282',permissionCount,denied,markerCreated:existsSync(marker),streamDeltas,cancellationMs,requestClosed,passed:true};
writeFileSync(path.join(root,'claude-protocol-test.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
