// Real SDK/tool/Stop-hook integration using an explicitly deterministic provider.
// This verifies control flow and execution evidence, not model intelligence.
import http from 'node:http';
import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = mkdtempSync(path.join(tmpdir(), 'opencore-feedback-test-'));
const source = path.join(root, 'main.mjs');
writeFileSync(path.join(root, 'check.mjs'), "import assert from 'node:assert/strict'; import {add} from './main.mjs'; assert.equal(add(2,3),5);\n");
let requests = 0, checks = 0, permissions = 0, sawStopFeedback = false, sawErrorResult = false, result;
let child, diagnostics = '';
const event = (res, value) => res.write(`event: ${value.type}\ndata: ${JSON.stringify(value)}\n\n`);
const server = http.createServer(async (req, res) => {
  let raw = ''; for await (const chunk of req) raw += chunk;
  const body = JSON.parse(raw || '{}');
  if (req.url.includes('count_tokens')) { res.writeHead(200, {'content-type':'application/json'}); res.end('{"input_tokens":100}'); return; }
  if (!req.url.startsWith('/v1/messages')) { res.writeHead(404); res.end('{}'); return; }
  requests += 1;
  res.writeHead(200, {'content-type':'text/event-stream'});
  event(res, {type:'message_start', message:{id:`msg_${requests}`,type:'message',role:'assistant',model:body.model,content:[],stop_reason:null,stop_sequence:null,usage:{input_tokens:100,output_tokens:0}}});
  let name, args;
  if (requests === 1) { name='Write'; args={file_path:source,content:'export const add = (a,b) => a-b;\n'}; }
  if (requests === 3 || requests === 5) {
    name='mcp__opencore__dev'; args={action:'run',command:'node check.mjs',verifyPaths:['main.mjs']};
    if (requests === 3) sawStopFeedback=JSON.stringify(body.messages).includes('no passing execution check');
  }
  if (requests === 4) {
    sawErrorResult=body.messages.some(message => Array.isArray(message.content) && message.content.some(part => part.type==='tool_result' && part.tool_use_id==='call_3' && part.is_error===true));
    name='Write'; args={file_path:source,content:'export const add = (a,b) => a+b;\n'};
  }
  if (name) {
    event(res,{type:'content_block_start',index:0,content_block:{type:'tool_use',id:`call_${requests}`,name,input:{}}});
    event(res,{type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:JSON.stringify(args)}});
    event(res,{type:'content_block_stop',index:0});
    event(res,{type:'message_delta',delta:{stop_reason:'tool_use',stop_sequence:null},usage:{output_tokens:30}});
  } else {
    event(res,{type:'content_block_start',index:0,content_block:{type:'text',text:''}});
    event(res,{type:'content_block_delta',index:0,delta:{type:'text_delta',text:requests===2?'Done.':'The check passed.'}});
    event(res,{type:'content_block_stop',index:0});
    event(res,{type:'message_delta',delta:{stop_reason:'end_turn',stop_sequence:null},usage:{output_tokens:5}});
  }
  event(res,{type:'message_stop'}); res.end();
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
try {
  child=spawn(process.execPath,[process.argv[2] ?? 'src-tauri/resources/claude/runner.mjs'],{stdio:['pipe','pipe','pipe'],windowsHide:true});
  child.stderr.on('data', bytes => diagnostics+=bytes);
  createInterface({input:child.stdout}).on('line', line => {
    const value=JSON.parse(line);
    if (value.kind==='permission') { permissions+=1; child.stdin.write(JSON.stringify({kind:'reply',id:value.id,value:true})+'\n'); }
    if (value.kind==='tool') {
      assert.equal(value.name,'dev'); assert.equal(value.args.command,'node check.mjs');
      const check=spawnSync(process.execPath,['check.mjs'],{cwd:root,encoding:'utf8',windowsHide:true});
      checks+=1;
      const checked=check.status===0 ? {'main.mjs':createHash('sha256').update(readFileSync(source)).digest('hex')} : {};
      child.stdin.write(JSON.stringify({kind:'reply',id:value.id,value:{exitCode:check.status,stdout:check.stdout,stderr:check.stderr,checked}})+'\n');
    }
    if (value.kind==='sdk' && value.message.type==='result') result=value.message;
  });
  child.stdin.write(JSON.stringify({kind:'start',cwd:root,configDir:path.join(root,'config'),conversationId:'coding-feedback-test',effort:'off',
    content:'Implement add and verify it.',instructions:'Use the provided tools.',gateway:`http://127.0.0.1:${server.address().port}`,
    tools:[{type:'function',function:{name:'dev',description:'Run the given check.',parameters:{type:'object',properties:{action:{type:'string'},command:{type:'string'},verifyPaths:{type:'array',items:{type:'string'}}},required:['action','command','verifyPaths']}}}]} )+'\n');
  const timer=setTimeout(() => child.kill(),30000);
  await new Promise(resolve => child.on('exit',resolve)); clearTimeout(timer);
  assert.ok(result && !result.is_error, diagnostics || JSON.stringify(result));
  assert.equal(requests,6); assert.equal(checks,2); assert.ok(permissions>=4);
  assert.ok(sawStopFeedback,'The SDK must return the bounded Stop-hook feedback to the model');
  assert.ok(sawErrorResult,'Failed executable checks must be delivered as tool errors');
  assert.match(readFileSync(source,'utf8'), /a\+b/);
  const report={provider:'deterministic mock',sdk:'0.3.282',requests,checks,permissions,sawStopFeedback,sawErrorResult,passed:true};
  writeFileSync(path.join(root,'coding-feedback-test.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({...report,evidence:path.join(root,'coding-feedback-test.json')}));
} finally { if (child && child.exitCode===null) child.kill(); server.closeAllConnections(); server.close(); }
