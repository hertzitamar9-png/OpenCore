import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('the real runner forwards activity and stops a looping agent without starting an external client', {skip:process.platform!=='win32'}, async t=>{
  const parent=fs.realpathSync(os.tmpdir()),root=fs.mkdtempSync(path.join(parent,'opencore-loop-runner-'));
  t.after(()=>{
    const resolved=fs.realpathSync(root);
    assert.ok(resolved.toLowerCase().startsWith((parent+path.sep+'opencore-loop-runner-').toLowerCase()));
    fs.rmSync(resolved,{recursive:true,force:true});
  });
  const source=path.dirname(fileURLToPath(import.meta.url));
  for(const name of ['runner.mjs','agent-loop-guard.mjs','context-budget.mjs','coding-verification.mjs'])
    fs.copyFileSync(path.join(source,name),path.join(root,name));
  const sdk=path.join(root,'node_modules','@anthropic-ai','claude-agent-sdk');
  const binary=path.join(root,'node_modules','@anthropic-ai','claude-agent-sdk-win32-x64');
  const zod=path.join(root,'node_modules','zod');
  for(const dir of [sdk,binary,zod,path.join(root,'work'),path.join(root,'config')])fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(binary,'opencore-agent.exe'),'fixture: never executed');
  fs.writeFileSync(path.join(sdk,'package.json'),JSON.stringify({type:'module',exports:'./index.mjs'}));
  fs.writeFileSync(path.join(zod,'package.json'),JSON.stringify({type:'module',exports:'./index.mjs'}));
  fs.writeFileSync(path.join(zod,'index.mjs'),'export const z={fromJSONSchema:()=>({shape:{}})};');
  fs.writeFileSync(path.join(sdk,'index.mjs'),`
    export const tool=()=>({});export const createSdkMcpServer=()=>({});
    export function query(){return {
      async *[Symbol.asyncIterator](){
        const plans=["I'll build a polished snake game from scratch.","I will create a complete snake game.",
          "Let me build the snake game with a high score.","I'll create a polished snake game.","I will build the snake game."];
        for(let i=0;i<plans.length;i++)yield {type:'assistant',message:{id:String(i),content:[{type:'text',text:plans[i]}]}};
        yield {type:'assistant',message:{id:'after-loop',content:[{type:'text',text:'SHOULD NOT RUN'}]}};
      },
      async interrupt(){},close(){},async getContextUsage(){return {totalTokens:1,maxTokens:8192};}
    }}
  `);
  const child=spawn(process.execPath,[path.join(root,'runner.mjs')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let stdout='',stderr='';child.stdout.on('data',chunk=>stdout+=chunk);child.stderr.on('data',chunk=>stderr+=chunk);
  child.stdin.write(JSON.stringify({kind:'start',cwd:path.join(root,'work'),configDir:path.join(root,'config'),
    conversationId:'test',content:'Make a snake game',effort:'off',instructions:'',tools:[]})+'\n');
  const exit=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{child.kill();reject(Error('Fixture runner did not stop'));},10000);
    child.on('error',error=>{clearTimeout(timeout);reject(error);});
    child.on('exit',code=>{clearTimeout(timeout);resolve(code);});
  });
  assert.equal(exit,0,stderr);
  const events=stdout.trim().split('\n').map(line=>JSON.parse(line));
  assert.ok(events.filter(event=>event.kind==='sdk').length>=4);
  assert.ok(events.some(event=>event.kind==='fatal'&&/repeated planning/i.test(event.error)));
  assert.ok(!stdout.includes('SHOULD NOT RUN'));
});
