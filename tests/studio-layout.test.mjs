import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { chromium } from 'playwright';

const css = (await Promise.all(['appearance.css', 'styles.css', 'WorkspacePanel.css',
  'GameDevStudio.css', 'MediaStudio.css', 'agent-platform.css', 'historic-dark.css']
  .map(name => readFile(new URL(`../src/${name}`, import.meta.url), 'utf8'))))
  .join('\n').replace(/@import\s+[^;]+;/g, '');
const musicHtml = (await readFile(new URL('../src-tauri/resources/studio/music_index.html', import.meta.url), 'utf8'))
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
const escapeAttribute = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;');
const sizes = [{ width: 1600, height: 1000 }, { width: 1280, height: 720 }, { width: 900, height: 540 }];

function shell(content, workspace) {
  return `<div id="root"><div class="app-window-frame"><div class="window-titlebar">OpenCore</div>
    <div class="opencore-shell"><nav class="conversation-app-rail"></nav><div class="opencore-section">
      <header class="section-header">Studio</header><div class="section-workspace-stage"><div class="section-main">${content}</div>
      ${workspace ? '<aside class="unified-workspace" style="--workspace-width:360px">Workspace</aside>' : ''}</div>
    </div><footer class="statusbar">Runtime status</footer></div></div></div>`;
}

async function openPage(browser, size, content, workspace = false) {
  const page = await browser.newPage({ viewport: size });
  await page.setContent(`<html data-platform-theme="dark"><head><style>${css}</style></head><body>${shell(content, workspace)}</body></html>`);
  return page;
}

test('Game Dev and Media forms can scroll to the final controls above the app footer', async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.OPENCORE_UI_TEST_BROWSER ? { channel: process.env.OPENCORE_UI_TEST_BROWSER } : {}) });
  try {
    for (const studio of ['game-dev-studio', 'media-studio']) for (const size of sizes) for (const workspace of [false, true]) {
      const page = await openPage(browser, size, `<section class="${studio}"><header><h1>Studio</h1></header>
        <div style="height:1500px;flex-shrink:0">Generation controls</div><button id="last-control">Final studio control</button></section>`, workspace);
      const studioBox = await page.locator(`.${studio}`).boundingBox();
      const mainBox = await page.locator('.section-main').boundingBox();
      assert.ok(Math.abs(studioBox.height - mainBox.height) < 1, `${studio}: page must fit the available workspace`);
      await page.mouse.move(mainBox.x + mainBox.width / 2, mainBox.y + mainBox.height / 2);
      await page.mouse.wheel(0, 4000);
      await page.waitForFunction(studioClass => {
        const studio = document.querySelector(`.${studioClass}`);
        const last = document.querySelector('#last-control').getBoundingClientRect();
        const bounds = studio.getBoundingClientRect();
        return studio.scrollTop > 0 && last.top >= bounds.top && last.bottom <= bounds.bottom;
      }, studio);
      assert.equal(await page.locator('.section-main').evaluate(element => element.scrollTop), 0, 'only the studio owns the page scroll');
      await page.close();
    }
  } finally { await browser.close(); }
});

test('YuE2 fills the workspace and scrolls its own controls and song history without an outer scrollbar', async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.OPENCORE_UI_TEST_BROWSER ? { channel: process.env.OPENCORE_UI_TEST_BROWSER } : {}) });
  try {
    for (const size of sizes) for (const workspace of [false, true]) {
      const page = await openPage(browser, size, `<section class="music-studio music-studio-embedded"><iframe title="YuE2 Music Studio" srcdoc="${escapeAttribute(musicHtml)}"></iframe></section>`, workspace);
      const mainBox = await page.locator('.section-main').boundingBox();
      const frameBox = await page.locator('iframe').boundingBox();
      assert.deepEqual(frameBox, mainBox, 'the YuE2 interface must use the entire available workspace');
      assert.equal(await page.locator('.music-studio').evaluate(element => element.scrollHeight > element.clientHeight), false, 'no second outer scroll area');
      await page.mouse.move(frameBox.x + frameBox.width / 2, frameBox.y + frameBox.height / 2);
      await page.mouse.wheel(0, 10000);
      const frame = page.frames().find(frame => frame.parentFrame());
      await frame.waitForFunction(() => {
        const songs = document.querySelector('#runs').getBoundingClientRect();
        return document.scrollingElement.scrollTop > 0 && songs.top >= 0 && songs.bottom <= innerHeight;
      });
      assert.equal(await page.locator('.music-studio').evaluate(element => element.scrollTop), 0, 'wheel input stays inside YuE2');
      await page.close();
    }
  } finally { await browser.close(); }
});
