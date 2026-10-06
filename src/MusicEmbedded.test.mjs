import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { expect, it, vi } from 'vitest';

function studio() {
  const html = readFileSync('src-tauri/resources/studio/music_index.html', 'utf8');
  const dom = new JSDOM(html, {runScripts: 'outside-only', url: 'http://127.0.0.1:7860'});
  const code = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\ninit\(\);\s*$/, '');
  dom.window.eval(code + `
    INFO = {defaults:{abc:{},semantic:{max_tokens:2000,min_tokens:0},ode_steps:32}};
    buildControls();
    window.controls = {renderStatus,cancelGeneration,poll,refreshInfo,setSnapshot(value){lastSnapshot=value;lastStatus=value.status;lastWorkerActive=value.worker_active===true;}};
  `);
  return {dom, controls: dom.window.controls};
}
async function close(dom) { await new Promise(resolve => setImmediate(resolve)); dom.window.close(); }

it('the embedded Cancel button responds before its HTTP request completes and blocks new work during cleanup', async()=>{
  const {dom, controls} = studio();
  let respond;
  const fetch = vi.fn().mockImplementation(()=>new Promise(resolve=>{respond=resolve;}));
  Object.assign(dom.window,{fetch});
  const running = {status:'running',worker_active:true,stage:'Verifying model files',started:Date.now()/1000};
  controls.setSnapshot(running); controls.renderStatus(running);
  const pending = controls.cancelGeneration();
  expect(dom.window.document.querySelector('#statusText')?.textContent).toBe('Cancelled · releasing resources…');
  expect(dom.window.document.querySelector('#go').disabled).toBe(true);
  expect(dom.window.document.querySelector('#planBtn').disabled).toBe(true);
  expect(dom.window.document.querySelector('#cancel').hidden).toBe(true);
  respond({ok:true,json:async()=>({ok:true,status:'cancelled',worker_active:true})});
  await pending;
  fetch.mockImplementation(url=>Promise.resolve({ok:true,json:async()=>url==='/api/history'?[]:url==='/api/info'?{model_loaded:false}:{status:'cancelled',worker_active:false}}));
  await controls.poll();
  expect(dom.window.document.querySelector('#statusText')?.textContent).toBe('Cancelled');
  expect(dom.window.document.querySelector('#go').disabled).toBe(false);
  await close(dom);
});

it('an old poll cannot undo a newer cancellation',async()=>{
  const {dom, controls} = studio();
  let stale;
  const fetch = vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{stale=resolve;}))
    .mockResolvedValue({ok:true,json:async()=>({status:'cancelled',worker_active:true})});
  Object.assign(dom.window,{fetch});
  const polling = controls.poll();
  await controls.cancelGeneration();
  stale({ok:true,json:async()=>({status:'running',worker_active:true,stage:'Verifying files'})});
  await polling;
  expect(dom.window.document.querySelector('#statusText')?.textContent).toBe('Cancelled · releasing resources…');
  await close(dom);
});

it('keeps the studio heading generic while hardware remains available in its tooltip',async()=>{
  const {dom, controls} = studio();
  Object.assign(dom.window,{fetch:vi.fn().mockResolvedValue({ok:true,json:async()=>({gpu:'Fixture GPU',vram_gib:16,model_loaded:false})})});
  await controls.refreshInfo();
  expect(dom.window.document.querySelector('#gpu')?.textContent).toBe('Local music runtime · model loads on first generate');
  expect(dom.window.document.querySelector('#gpu').title).toBe('Detected hardware: Fixture GPU · 16 GB');
  await close(dom);
});
