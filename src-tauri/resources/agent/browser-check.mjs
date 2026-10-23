// Real Chromium execution for local HTML deliverables. No user browser profile,
// extension, API key, or npm dependency is required.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const within = (root, target) => {
  const relative = path.relative(root, target);
  return !path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep);
};
const limit = (value, fallback, minimum, maximum) => Math.min(maximum, Math.max(minimum, Number(value) || fallback));
const controlInfo = `el => {
  const box=el.getBoundingClientRect(),style=getComputedStyle(el);
  const text=(el.innerText||el.value||el.getAttribute('aria-label')||'').trim();
  const x=(Math.max(0,box.left)+Math.min(innerWidth,box.right))/2;
  const y=(Math.max(0,box.top)+Math.min(innerHeight,box.bottom))/2;
  const visible=box.width>0&&box.height>0&&box.bottom>0&&box.right>0&&box.top<innerHeight&&box.left<innerWidth&&style.visibility!=='hidden'&&Number(style.opacity)!==0;
  const hit=visible?document.elementFromPoint(x,y):null;
  return {text,x,y,layout:box.width>0&&box.height>0,usable:visible&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'&&(hit===el||el.contains(hit))};
}`;

async function browserExecutable() {
  const candidates = process.platform === 'win32'
    ? ['ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'].flatMap(name => process.env[name] ? [
      path.join(process.env[name], 'Google/Chrome/Application/chrome.exe'),
      path.join(process.env[name], 'Microsoft/Edge/Application/msedge.exe'),
    ] : [])
    : process.platform === 'darwin'
      ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
      : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/microsoft-edge'];
  for (const candidate of candidates) {
    try { if ((await fs.stat(candidate)).isFile()) return candidate; } catch {}
  }
  throw new Error('HTML execution needs an installed Chrome, Edge, or Chromium browser. Browser testing is unavailable; the game has not been verified.');
}

async function connectCdp(url, onEvent) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser debugger connection timed out')), 5000);
    socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Browser debugger connection failed')); }, { once: true });
  });
  let serial = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const task = pending.get(message.id);
      if (!task) return;
      clearTimeout(task.timer); pending.delete(message.id);
      message.error ? task.reject(new Error(message.error.message)) : task.resolve(message.result);
    } else onEvent(message.method, message.params || {});
  });
  socket.addEventListener('close', () => {
    for (const task of pending.values()) { clearTimeout(task.timer); task.reject(new Error('Browser debugger closed')); }
    pending.clear();
  });
  return {
    send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++serial;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Browser command timed out: ${method}`)); }, 5000);
        pending.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    close() { socket.close(); },
  };
}

export async function runBrowserCheck(options) {
  const started = Date.now();
  const root = await fs.realpath(options.root);
  const target = await fs.realpath(path.resolve(root, options.path));
  if (!within(root, target) || !/\.html?$/i.test(target)) throw new Error('Use an HTML file inside the chat workspace');
  const executable = await browserExecutable();
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'opencore-game-check-'));
  const nonce = randomUUID();
  const servedSources = new Map();
  const mime = { '.html': 'text/html', '.htm': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.wasm': 'application/wasm', '.mp3': 'audio/mpeg', '.wav': 'audio/wav' };
  const server = http.createServer(async (request, response) => {
    try {
      if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405).end(); return; }
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const entry = pathname.startsWith('/' + nonce + '/');
      const hasSession = String(request.headers.cookie || '').split(';').some(cookie => cookie.trim() === `OpenCoreBrowserCheck=${nonce}`);
      if (!entry && !hasSession) { response.writeHead(pathname === '/favicon.ico' ? 204 : 404).end(); return; }
      const relative = entry ? pathname.slice(nonce.length + 2) : pathname.slice(1);
      const resolved = await fs.realpath(path.resolve(root, relative));
      const info = await fs.stat(resolved);
      if (!within(root, resolved) || !info.isFile()) { response.writeHead(403).end(); return; }
      if (info.size > 32 * 1024 * 1024) { response.writeHead(413).end(); return; }
      const bytes = await fs.readFile(resolved);
      servedSources.set(path.relative(root, resolved).replaceAll('\\', '/'), hash(bytes));
      response.writeHead(200, { 'Content-Type': mime[path.extname(resolved).toLowerCase()] || 'application/octet-stream', 'Cache-Control': 'no-store',
        ...(entry ? { 'Set-Cookie': `OpenCoreBrowserCheck=${nonce}; HttpOnly; SameSite=Strict; Path=/` } : {}) });
      response.end(request.method === 'HEAD' ? undefined : bytes);
    } catch { response.writeHead(request.url === '/favicon.ico' ? 204 : 404).end(); }
  });
  let child, cdp;
  let deadline;
  const issues = new Map();
  const counts = { runtimeErrors: 0, consoleErrors: 0, networkErrors: 0, warnings: 0 };
  const interactions = [];
  function issue(kind, message, source = '', line, column, stack = '') {
    const countKey = { exception: 'runtimeErrors', console: 'consoleErrors', network: 'networkErrors', warning: 'warnings' }[kind];
    if (countKey) counts[countKey]++;
    const key = JSON.stringify([kind, message, source, line, column]);
    const known = issues.get(key);
    if (known) { known.count++; known.lastMs = Date.now() - started; return; }
    if (issues.size >= 256) return;
    issues.set(key, { kind, message: String(message).slice(0, 800), source: String(source).slice(0, 300), line, column,
      stack: String(stack).slice(0, 1200), count: 1, firstMs: Date.now() - started, lastMs: Date.now() - started });
  }
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    child = spawn(executable, ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-sync',
      '--disable-extensions', '--disable-background-networking', '--disable-component-update', '--mute-audio',
      '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--window-size=1280,720',
      '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
    { stdio: 'ignore', windowsHide: true });
    let launchError;
    child.on('error', error => { launchError = error; });
    const run = async () => {
      let port;
      for (let attempt = 0; attempt < 100; attempt++) {
        if (launchError) throw launchError;
        if (child.exitCode !== null) throw new Error(`Test browser exited: ${child.exitCode}`);
        try { port = Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); if (port) break; } catch {}
        await pause(75);
      }
      if (!port) throw new Error('Test browser did not start its debugger');
      const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json());
      const page = targets.find(page => page.type === 'page' && page.url === 'about:blank');
      if (!page) throw new Error('Test browser did not create an isolated page');
      cdp = await connectCdp(page.webSocketDebuggerUrl, (method, event) => {
        if (method === 'Runtime.exceptionThrown') {
          const detail = event.exceptionDetails;
          const first = detail.stackTrace?.callFrames?.[0];
          issue('exception', detail.exception?.description?.split('\n')[0] || detail.text, detail.url || first?.url,
            (detail.lineNumber ?? first?.lineNumber ?? 0) + 1, (detail.columnNumber ?? first?.columnNumber ?? 0) + 1,
            detail.exception?.description || '');
        } else if (method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(event.type)) {
          const frame = event.stackTrace?.callFrames?.[0];
          issue(event.type === 'error' ? 'console' : 'warning', event.args.map(arg => arg.value ?? arg.description ?? arg.type).join(' '),
            frame?.url, frame ? frame.lineNumber + 1 : undefined, frame ? frame.columnNumber + 1 : undefined);
        } else if (method === 'Network.loadingFailed' && !event.canceled) issue('network', event.errorText, event.type);
        else if (method === 'Network.responseReceived' && event.response.status >= 400)
          issue('network', `HTTP ${event.response.status}`, event.response.url);
      });
      await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable');
      await cdp.send('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1,mobile:false});
      const url = `http://127.0.0.1:${server.address().port}/${nonce}/${path.relative(root, target).split(path.sep).map(encodeURIComponent).join('/')}`;
      const navigated = await cdp.send('Page.navigate', { url });
      if (navigated.errorText) throw new Error(navigated.errorText);
      const frame=await cdp.send('Page.getFrameTree');
      const world=await cdp.send('Page.createIsolatedWorld',{frameId:frame.frameTree.frame.id,worldName:'opencore-html-check-v1'});
      const evaluate = async expression => {
        const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, contextId:world.executionContextId });
        if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
        return result.result?.value;
      };
      for (let attempt = 0; attempt < 60; attempt++) {
        if (await evaluate("document.readyState === 'complete'")) break;
        if (attempt === 59) throw new Error('Game page did not finish loading');
        await pause(100);
      }
      await pause(500);
      const click = async text => {
        const resolve=()=>evaluate(`(() => {
          const matches=[...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')].map(${controlInfo}).filter(item=>item.text===${JSON.stringify(text)}&&item.usable);
          return matches.length===1?matches[0]:null;
        })()`);
        let target=await resolve();
        if(!target)return {clicked:false,reason:'The control is hidden, disabled, covered, outside the viewport, or ambiguous'};
        for(let attempt=0;attempt<3;attempt++){
          await cdp.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:target.x,y:target.y});
          const current=await resolve();
          if(!current)return {clicked:false,reason:'The control became covered or unavailable after pointer movement'};
          if(Math.abs(current.x-target.x)<1&&Math.abs(current.y-target.y)<1){
            await cdp.send('Input.dispatchMouseEvent',{type:'mousePressed',x:current.x,y:current.y,button:'left',clickCount:1});
            await cdp.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:current.x,y:current.y,button:'left',clickCount:1});
            return {clicked:true,text,x:current.x,y:current.y};
          }
          target=current;
        }
        return {clicked:false,reason:'The control keeps moving under the pointer'};
      };
      const steps = Array.isArray(options.steps) ? options.steps : [];
      if (steps.length > 24) throw new Error('Use at most 24 browser test steps');
      if (!steps.length) {
        const primary = await evaluate(`(() => {
          const buttons=[...document.querySelectorAll('button,[role="button"],input[type="button"]')].map(${controlInfo})
            .filter(item=>item.layout&&/^(start(?: game)?|play(?: game)?|begin)$/i.test(item.text));
          const usable=buttons.filter(item=>item.usable);
          if(usable.length===1)return {text:usable[0].text};
          return buttons.length?{error:'Start/Play is covered, unavailable, outside the viewport, or ambiguous; supply explicit browser steps'}:null;
        })()`);
        if(primary?.error)throw new Error(primary.error);
        if(primary){const result=await click(primary.text);interactions.push({automatic:true,...result});if(!result.clicked)throw new Error(result.reason);}
      }
      for (const step of steps) {
        if (typeof step.clickText === 'string') {
          const result = await click(step.clickText);
          interactions.push(result);
          if (!result.clicked) throw new Error(`Cannot click ${step.clickText}: ${result.reason}`);
        } else if (typeof step.key === 'string') {
          const key = step.key === 'Space' ? ' ' : step.key;
          const code = key.length === 1 && /[a-z]/i.test(key) ? 'Key' + key.toUpperCase() : (/^[0-9]$/.test(key)?'Digit'+key:step.key);
          const virtualKey={ArrowLeft:37,ArrowUp:38,ArrowRight:39,ArrowDown:40,Space:32,Enter:13,Escape:27,Tab:9,Backspace:8}[step.key] || (key.length===1?key.toUpperCase().charCodeAt(0):0);
          const event={key,code,...(virtualKey?{windowsVirtualKeyCode:virtualKey}:{})};
          await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...event, ...(key.length===1?{text:key,unmodifiedText:key}:{}) });
          await pause(limit(step.holdMs, 120, 1, 1000));
          await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...event });
          interactions.push({ key: step.key });
        } else if(Number.isFinite(step.scrollY)){
          await cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:640,y:360,deltaX:0,deltaY:Math.max(-4000,Math.min(4000,step.scrollY))});
          interactions.push({scrollY:step.scrollY});
        } else if (Number.isFinite(step.x) && Number.isFinite(step.y) && step.x >= 0 && step.y >= 0 && step.x < 1280 && step.y < 720) {
          await cdp.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:step.x,y:step.y});
          await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: step.x, y: step.y, button: 'left', clickCount: 1 });
          await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: step.x, y: step.y, button: 'left', clickCount: 1 });
          interactions.push({ x: step.x, y: step.y });
        } else if (!Number.isFinite(step.waitMs)) throw new Error('A browser test step needs clickText, key, coordinates, or waitMs');
        await pause(limit(step.waitMs, 120, 1, 1500));
        if (typeof step.expectText === 'string') {
          const found = await evaluate(`(document.body?.innerText || '').includes(${JSON.stringify(step.expectText)})`);
          interactions.push({ expectedText: step.expectText, passed: found });
          if (!found) throw new Error(`Expected game text did not appear: ${step.expectText}`);
        }
      }
      await pause(limit(options.observeMs, 2200, 500, 10000));
      const state = await evaluate(`({ url: location.href, title: document.title, text: (document.body?.innerText || '').slice(0, 6000), canvases: document.querySelectorAll('canvas').length, controls: [...document.querySelectorAll('button,input,[role="button"]')].slice(0,20).map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').slice(0,100)) })`);
      if (state.url !== url) issue('exception', 'Game navigated away from the tested local file', state.url);
      let screenshotPath;
      if (options.screenshotPath) {
        const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png' });
        await fs.writeFile(options.screenshotPath, Buffer.from(screenshot.data, 'base64'));
        screenshotPath = options.screenshotPath;
      }
      for (const [name, digest] of servedSources) {
        const current = await fs.realpath(path.resolve(root, name));
        if (!within(root, current) || hash(await fs.readFile(current)) !== digest) throw new Error('Game sources changed during browser testing; rerun the check');
      }
      const errors = counts.runtimeErrors + counts.consoleErrors + counts.networkErrors;
      return { exitCode: errors ? 1 : 0, passed: errors === 0, ...state, counts, issues: [...issues.values()].sort((a, b) => (a.kind === 'warning') - (b.kind === 'warning')).slice(0, 12),
        uniqueIssues: issues.size, omittedUniqueIssues: Math.max(0, issues.size - 12), interactions, screenshotPath,
        interactionTested:interactions.some(item=>item.clicked||item.key||Number.isFinite(item.x)),
        checked: Object.fromEntries(servedSources), durationMs: Date.now() - started, browser: path.basename(executable),
        evidence: 'Executed the actual HTML in an isolated Chromium profile. Checks cover the reported load and interactions; they do not prove every gameplay path.' };
    };
    return await Promise.race([run(), new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Browser check exceeded 30 seconds')), 30000); })]);
  } catch (error) {
    return { exitCode: 1, passed: false, error: error.message, counts, issues: [...issues.values()].slice(0, 12),
      uniqueIssues: issues.size, interactions, checked: {}, durationMs: Date.now() - started };
  } finally {
    clearTimeout(deadline);
    if (cdp) { await cdp.send('Browser.close').catch(() => {}); cdp.close(); }
    if (child && child.exitCode === null) { await Promise.race([new Promise(resolve => child.once('exit', resolve)), pause(1500)]); if (child.exitCode === null) child.kill(); }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    // Only the temporary profile created by this check is eligible for removal.
    if (path.dirname(profile) === path.resolve(os.tmpdir()) && path.basename(profile).startsWith('opencore-game-check-'))
      await fs.rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }).catch(() => {});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    const options = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
    const result = await runBrowserCheck(options);
    process.stdout.write(JSON.stringify(result));
    process.exitCode = result.exitCode;
  } catch (error) {
    process.stdout.write(JSON.stringify({ exitCode: 1, passed: false, error: error.message }));
    process.exitCode = 1;
  }
}
