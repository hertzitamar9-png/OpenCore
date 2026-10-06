// Called by the Windows Rust integration test on GitHub Actions, against the real file server.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { chromium } from 'playwright';

if (process.env.GITHUB_ACTIONS !== 'true') throw new Error('Run the native/browser confinement gate on GitHub Actions only.');
const [capturedUrl, otherCaptureUrl] = process.argv.slice(2);
for (const value of [capturedUrl, otherCaptureUrl]) {
  const parsed = new URL(value);
  assert.equal(parsed.protocol, 'http:');
  assert.equal(parsed.hostname, '127.0.0.1');
}
assert.notEqual(new URL(capturedUrl).origin, new URL(otherCaptureUrl).origin);

// A permissive dummy API proves that the captured page blocks requests before they reach a server.
// No model endpoint or model job is involved.
const received = [];
const sentinel = createServer((request, response) => {
  received.push({ method: request.method, path: request.url });
  response.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' });
  response.end('{"unexpected":"request reached dummy API"}');
});
await new Promise((resolve, reject) => { sentinel.once('error', reject); sentinel.listen(0, '127.0.0.1', resolve); });
const dummyApi = `http://127.0.0.1:${sentinel.address().port}`;
let browser;
try {
  browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  await page.goto(capturedUrl, { waitUntil: 'load' });
  assert.equal(await page.evaluate(() => document.documentElement.dataset.inlineScript), 'yes');
  assert.equal(await page.evaluate(() => document.documentElement.dataset.recordedScript), 'yes');
  assert.equal(await page.locator('#ready').evaluate(element => getComputedStyle(element).color), 'rgb(1, 2, 3)');
  assert.equal(await page.locator('#recorded-image').evaluate(element => element.naturalWidth), 32);

  const png = await readFile(new URL('../src-tauri/icons/32x32.png', import.meta.url));
  await page.route('https://cdn.jsdelivr.net/npm/opencore-csp-fixture/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('.js')) return route.fulfill({ contentType: 'text/javascript', body: "document.documentElement.dataset.cdnScript='yes';" });
    if (pathname.endsWith('.css')) return route.fulfill({ contentType: 'text/css', body: '#ready { background-color: rgb(4, 5, 6); }' });
    return route.fulfill({ contentType: 'image/png', body: png });
  });
  const cdnLoaded = await page.evaluate(async () => {
    const load = (element, property, file) => new Promise(resolve => {
      element.onload = () => resolve(true);
      element.onerror = () => resolve(false);
      element[property] = `https://cdn.jsdelivr.net/npm/opencore-csp-fixture/${file}`;
      document.head.append(element);
    });
    const stylesheet = document.createElement('link'); stylesheet.rel = 'stylesheet';
    return Promise.all([
      load(document.createElement('script'), 'src', 'allowed.js'),
      load(stylesheet, 'href', 'allowed.css'),
      load(document.createElement('img'), 'src', 'allowed.png'),
    ]);
  });
  assert.deepEqual(cdnLoaded, [true, true, true]);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.cdnScript), 'yes');
  assert.equal(await page.locator('#ready').evaluate(element => getComputedStyle(element).backgroundColor), 'rgb(4, 5, 6)');

  const outcomes = await page.evaluate(async ({ dummyApi, otherCaptureUrl }) => {
    window.blockedDirectives = [];
    document.addEventListener('securitypolicyviolation', event => window.blockedDirectives.push(event.effectiveDirective));
    const posted = fetch(`${dummyApi}/v1/chat/completions`, {
      method: 'POST', headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ messages: [{ role: 'user', content: 'dummy request' }] }),
    }).then(() => true, () => false);
    const imported = fetch(`${dummyApi}/echo/import`, {
      method: 'POST', mode: 'no-cors', body: JSON.stringify({ conversation_id: 'dummy', messages: [] }),
    }).then(() => true, () => false);
    const crossCapture = fetch(otherCaptureUrl).then(() => true, () => false);
    navigator.sendBeacon(`${dummyApi}/beacon`, 'dummy');
    const frame = document.createElement('iframe'); frame.name = 'blocked-form-target';
    frame.src = `${dummyApi}/frame`; document.body.append(frame);
    const form = document.createElement('form'); form.method = 'POST'; form.action = `${dummyApi}/form`;
    form.target = frame.name; document.body.append(form); form.submit();
    const image = document.createElement('img'); image.src = `${dummyApi}/image`; document.body.append(image);
    const script = document.createElement('script'); script.src = `${dummyApi}/script.js`; document.body.append(script);
    const video = document.createElement('video'); video.src = `${dummyApi}/media`; video.load(); document.body.append(video);
    const [modelPost, memoryImport, otherCapture] = await Promise.all([posted, imported, crossCapture]);
    return { modelPost, memoryImport, otherCapture };
  }, { dummyApi, otherCaptureUrl });
  assert.deepEqual(outcomes, { modelPost: false, memoryImport: false, otherCapture: false }, 'A captured script could send a model/API request or fetch another capture.');
  await page.waitForFunction(() => ['connect-src', 'frame-src', 'form-action', 'img-src', 'script-src-elem', 'media-src']
    .every(directive => window.blockedDirectives.includes(directive)));
  assert.deepEqual(received, [], 'An unrecorded loopback API received browser traffic.');

  const otherPage = await context.newPage();
  await otherPage.goto(otherCaptureUrl, { waitUntil: 'load' });
  assert.equal(await otherPage.evaluate(() => localStorage.getItem('capture-only')), null, 'Separate captures shared browser storage.');
  console.log(JSON.stringify({ recordedHtmlScriptsCssPng: 'passed', httpsCdnAssets: 'passed', modelPostsAndMemoryImport: 'blocked', loopbackFormsFramesImagesScriptsMedia: 'blocked', captureStorageIsolation: 'passed' }));
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => sentinel.close(resolve));
}
