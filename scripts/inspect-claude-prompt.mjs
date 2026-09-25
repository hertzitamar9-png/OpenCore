import http from 'node:http';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';
const folder=path.resolve('artifacts/claude-prompt-probe');mkdirSync(folder,{recursive:true});
let child;
const server=http.createServer(async(req,res)=>{
  let raw='';for await(const data of req)raw+=data;
  const body=JSON.parse(raw||'{}');
  if(req.url.startsWith('/v1/messages')&&!req.url.includes('count_tokens')){
    const stats={systemChars:JSON.stringify(body.system).length,tools:(body.tools??[]).map(t=>({name:t.name,chars:JSON.stringify(t).length})),messageChars:JSON.stringify(body.messages).length};
    console.log(JSON.stringify(stats));writeFileSync(path.join(folder,'sizes.json'),JSON.stringify(stats,null,2));
    child.stdin.write(JSON.stringify({kind:'cancel'})+'\n');
    setTimeout(()=>server.close(),1000);
  }
  res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({type:'error',error:{type:'invalid_request_error',message:'Prompt inspection complete; no model request sent'}}));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
child=spawn(process.execPath,['src-tauri/resources/claude/runner.mjs'],{stdio:['pipe','pipe','pipe']});
child.stdout.on('data',d=>{for(const line of d.toString().split('\n')){try{const e=JSON.parse(line);if(e.kind==='context')console.log(JSON.stringify(e));}catch{}}});child.stderr.on('data',d=>process.stderr.write(d));
child.stdin.write(JSON.stringify({kind:'start',cwd:folder,configDir:path.join(folder,'config'),conversationId:'probe',effort:'low',gateway:`http://127.0.0.1:${server.address().port}`,content:'Read movement.py',tools:[],instructions:'OpenCore.'})+'\n');
setTimeout(()=>{child.kill();server.close();},15000).unref();
