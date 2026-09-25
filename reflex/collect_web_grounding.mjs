// Collect GUI grounding samples from public web pages in a hidden browser.
//
// usage: node collect_web_grounding.mjs OUT_DIR URL_FILE [--split eval|train]
//        [--follow N] [--scrolls K] [--locale he-IL]
//
// For each page view: one viewport screenshot and every visible, unobscured control whose
// name is unique in that view (so the instruction has exactly one right answer), with its
// box in screenshot pixels. --follow visits up to N more same-site pages linked from each
// seed; --scrolls also records K-1 views further down the page. Nothing is clicked or
// typed. Output: OUT_DIR/<split>.jsonl plus OUT_DIR/images/*.png.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const [, , outDir, urlFile, ...rest] = process.argv;
const option = (name, fallback) => (rest.includes(name) ? rest[rest.indexOf(name) + 1] : fallback);
const split = option('--split', 'eval');
const follow = Number(option('--follow', '0'));
const scrolls = Number(option('--scrolls', '1'));
const locale = option('--locale', 'he-IL');
const seeds = fs.readFileSync(urlFile, 'utf8').split(/\r?\n/).map((line) => line.trim())
  .filter((line) => line && !line.startsWith('#'));
const viewports = [[1920, 1080], [1366, 768], [2560, 1440], [1600, 900], [1280, 800]];
fs.mkdirSync(path.join(outDir, 'images'), { recursive: true });
const out = fs.createWriteStream(path.join(outDir, `${split}.jsonl`), { flags: 'a' });
const done = new Set(fs.existsSync(path.join(outDir, `${split}.jsonl`))
  ? fs.readFileSync(path.join(outDir, `${split}.jsonl`), 'utf8').split('\n').filter(Boolean)
    .map((line) => JSON.parse(line).image) : []);

function collect() {
  const selector = 'a,button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],'
    + '[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],[role=searchbox],[onclick]';
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const rows = [];
  for (const element of document.querySelectorAll(selector)) {
    const box = element.getBoundingClientRect();
    if (box.width < 8 || box.height < 8 || box.left < 0 || box.top < 0
        || box.right > innerWidth || box.bottom > innerHeight) continue;
    if (!element.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    if (!hit || !(hit === element || element.contains(hit))) continue;
    const text = clean(element.innerText || (element.type !== 'password' ? element.value : ''));
    const image = element.querySelector('img[alt]');
    const label = clean(element.getAttribute('aria-label') || element.getAttribute('title')
      || element.getAttribute('placeholder') || (image && image.getAttribute('alt')) || '');
    const name = text || label;
    if (!name || name.length < 2 || name.length > 50) continue;
    rows.push({ name, kind: text ? 'text' : 'icon', tag: element.tagName.toLowerCase(),
      role: element.getAttribute('role') || '', type: element.getAttribute('type') || '',
      box: [box.left, box.top, box.right, box.bottom].map(Math.round) });
  }
  const links = [...document.querySelectorAll('a[href]')].map((a) => a.href)
    .filter((href) => href.startsWith(location.origin) && !href.includes('#'));
  return { rows, links, dir: getComputedStyle(document.body || document.documentElement).direction };
}

const browser = await chromium.launch({
  headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
});
let views = 0; let samples = 0; let visit = 0;
for (const seed of seeds) {
  const queue = [seed]; const seen = new Set([seed]); let visited = 0;
  while (queue.length && visited <= follow) {
    const url = queue.shift(); visited += 1; visit += 1;
    const [width, height] = viewports[visit % viewports.length];
    const context = await browser.newContext({
      viewport: { width, height }, locale, deviceScaleFactor: 1,
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36',
    });
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(3000);
      for (let view = 0; view < scrolls; view += 1) {
        if (view > 0) {
          await page.evaluate(() => window.scrollBy(0, Math.round(innerHeight * 0.9)));
          await page.waitForTimeout(1200);
        }
        const found = await page.evaluate(collect);
        if (view === 0) {
          for (const link of found.links.sort(() => Math.random() - 0.5)) {
            if (!seen.has(link) && queue.length < follow * 3) { seen.add(link); queue.push(link); }
          }
        }
        const counts = new Map();
        for (const row of found.rows) counts.set(row.name, (counts.get(row.name) || 0) + 1);
        const unique = found.rows.filter((row) => counts.get(row.name) === 1);
        if (unique.length < 3) { console.log(`skip ${url} view ${view}: ${unique.length} usable controls`); continue; }
        const id = crypto.createHash('sha1').update(`${page.url()} ${width} ${view}`).digest('hex').slice(0, 14);
        const image = `images/${id}.png`;
        if (done.has(image)) continue;
        done.add(image);
        await page.screenshot({ path: path.join(outDir, image) });
        const site = new URL(page.url()).hostname.replace(/^www\./, '');
        for (const row of unique) {
          out.write(JSON.stringify({ image, url: page.url(), site, split, size: [width, height], dir: found.dir,
            lang: /[\u0590-\u05FF]/.test(row.name) ? 'he' : 'other', ...row }) + '\n');
        }
        views += 1; samples += unique.length;
        console.log(`${site} ${width}x${height} view ${view} ${found.dir} ${unique.length} controls`);
      }
    } catch (error) {
      console.log(`fail ${url}: ${String(error.message || error).split('\n')[0]}`);
    } finally {
      await context.close();
    }
  }
}
await browser.close();
out.end();
console.log(`views=${views} samples=${samples}`);
