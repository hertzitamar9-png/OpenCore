// Real SDK protocol test: a queued generation must end the inference turn.
import http from 'node:http';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=mkdtempSync(path.join(tmpdir(),'opencore-handoff-'));
let requests=0, handoff, result, diagnostics='';
function event(res,value){res.write(`event: ${value.type}\ndata: ${JSON.stringify(value)}\n\n`);}
const server=http.createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  const body=JSON.parse(raw||'{}');
  if(req.url.includes('count_tokens')){res.end('{"input_tokens":100}');return;}
  if(!req.url.startsWith('/v1/messages')){res.writeHead(404);res.end('{}');return;}
  requests++;
  res.writeHead(200,{'content-type':'text/event-stream'});
  event(res,{type:'message_start',message:{id:`msg_${requests}`,type:'message',role:'assistant',model:body.model,content:[],stop_reason:null,stop_sequence:null,usage:{input_tokens:100,output_tokens:0}}});
  if(requests===1){
    event(res,{type:'content_block_start',index:0,content_block:{type:'tool_use',id:'song',name:'mcp__opencore__music_generate',input:{}}});
    event(res,{type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:JSON.stringify({prompt:'AI song'})}});
    event(res,{type:'content_block_stop',index:0});
    event(res,{type:'message_delta',delta:{stop_reason:'tool_use',stop_sequence:null},usage:{output_tokens:10}});
  }else{
    event(res,{type:'content_block_start',index:0,content_block:{type:'text',text:''}});
    event(res,{type:'content_block_delta',index:0,delta:{type:'text_delta',text:'I am checking the job again.'}});
    event(res,{type:'content_block_stop',index:0});
    event(res,{type:'message_delta',delta:{stop_reason:'end_turn',stop_sequence:null},usage:{output_tokens:5}});
  }
  event(res,{type:'message_stop'});res.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const child=spawn(process.execPath,['src-tauri/resources/claude/runner.mjs'],{stdio:['pipe','pipe','pipe']});
child.stderr.on('data',b=>diagnostics+=b);
createInterface({input:child.stdout}).on('line',line=>{
  const value=JSON.parse(line);
  if(value.kind==='permission')child.stdin.write(JSON.stringify({kind:'reply',id:value.id,value:true})+'\n');
  if(value.kind==='tool')child.stdin.write(JSON.stringify({kind:'reply',id:value.id,value:{id:'job-1',category:'music',status:'queued',request:{conversationId:'handoff'}}})+'\n');
  if(value.kind==='handoff')handoff=value;
  if(value.kind==='sdk'&&value.message.type==='result')result=value.message;
  if(value.kind==='fatal')diagnostics+=value.error;
});
child.stdin.write(JSON.stringify({kind:'start',cwd:root,configDir:path.join(root,'config'),conversationId:'handoff',effort:'off',content:'Make the AI song.',instructions:'Use music_generate.',gateway:`http://127.0.0.1:${server.address().port}`,tools:[{type:'function',function:{name:'music_generate',description:'Queue a song',parameters:{type:'object',properties:{prompt:{type:'string'}},required:['prompt']}}}]})+'\n');
const timeout=setTimeout(()=>child.kill(),30000);
await new Promise(resolve=>child.on('exit',resolve));clearTimeout(timeout);
server.closeAllConnections();server.close();
assert.ok(result&&!result.is_error,diagnostics||JSON.stringify(result));
assert.equal(requests,1,'A queued job must hand off without another inference request');
assert.equal(handoff?.jobId,'job-1');
assert.equal(handoff?.category,'music');
console.log(JSON.stringify({requests,handoff,passed:true}));
