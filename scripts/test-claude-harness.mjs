import { chromium } from 'playwright';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
const browser = await chromium.connectOverCDP('http://127.0.0.1:9227');
const page = browser.contexts()[0].pages().find(p => p.url() === 'http://tauri.localhost/');
if (!page) throw new Error('OpenCore webview not found');
const id = 'opencore:' + randomUUID();
const workspace = path.join(process.env.APPDATA, 'ai.opencore.control-center', 'code-workspaces', createHash('sha256').update(id).digest('hex').slice(0,24));
mkdirSync(workspace, {recursive:true});
writeFileSync(path.join(workspace,'movement.py'), 'MARKER = "keep-me-731"\n\ndef running(speed):\n    return speed * 2\n\ndef walking(speed):\n    return speed\n');
const record = { id, workspace, started: new Date().toISOString(), turns: [] };
mkdirSync('artifacts',{recursive:true});
writeFileSync('artifacts/claude-harness-active.json', JSON.stringify(record,null,2));
console.log(JSON.stringify({id,workspace}));
async function send(text) {
  const started = Date.now();
  const result = await page.evaluate(async ({id,text}) => {
    const samples = [];
    const internal = window.__TAURI_INTERNALS__;
    const callback = internal.transformCallback(event => { const p=event.payload; if(p.conversationId===id && (p.content || p.reasoning)) samples.push({ms:Date.now(),length:p.content?.length ?? 0,reasoningLength:p.reasoning?.length ?? 0}); });
    const eventId = await internal.invoke('plugin:event|listen', {event:'opencore-generation',target:{kind:'Any'},handler:callback});
    try { return {response:await internal.invoke('send_chat_message',{request:{conversationId:id,text,files:[],reasoningEffort:'low',approvalMode:'allow-all',skills:[]}}),samples}; }
    catch(error) { return {error:String(error),samples}; }
    finally { await internal.invoke('plugin:event|unlisten',{event:'opencore-generation',eventId}); }
  },{id,text});
  record.turns.push({text,...result,durationMs:Date.now()-started,file:readFileSync(path.join(workspace,'movement.py'),'utf8')});
  writeFileSync('artifacts/claude-harness-e2e.json',JSON.stringify(record,null,2));
  console.log(JSON.stringify(record.turns.at(-1)));
  if(result.error) throw new Error(result.error);
}
try {
  await send('In the existing movement.py file, change running(speed) to return speed * 3. Keep walking and MARKER unchanged. Read the file first, make a focused edit, and run Python assertions to check running(4)==12, walking(4)==4, and MARKER=="keep-me-731". Use the native file and shell tools. Report the actual check result.');
  await send('Now change only walking(speed) to return speed / 2 in that same file. Keep the previous running change and the MARKER. Run Python assertions for all three values.');
  record.timeline = await page.evaluate(id=>window.__TAURI_INTERNALS__.invoke('get_conversation',{conversationId:id}),id);
  writeFileSync('artifacts/claude-harness-e2e.json',JSON.stringify(record,null,2));
} finally { await browser.close(); }
