import { chromium } from "playwright";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, resolve, sep } from "node:path";

const token = process.argv[2];
if (!/^[0-9a-f-]{36}$/i.test(token || "")) throw new Error("Pass the temporary pairing code from the Browser panel");
const profile = await mkdtemp(resolve(tmpdir(), "opencore-chrome-smoke-"));
const extension = resolve("chrome-extension");
const server = createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  response.end('<!doctype html><title>OpenCore browser smoke</title><button id="test" onclick="document.getElementById(\'result\').textContent=\'Clicked\'">Click me</button><input id="field" placeholder="Type here"><output id="result">Ready</output>');
});
await new Promise((accept) => server.listen(8989, "127.0.0.1", accept));
let context;
try {
  context = await chromium.launchPersistentContext(profile, {
    headless: false,
    executablePath: process.env.OPENCORE_CHROME_EXECUTABLE || chromium.executablePath(),
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker", { timeout: 15000 });
  const extensionId = new URL(worker.url()).host;
  const popup = await context.newPage();
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup.locator("#token").fill(token);
  await popup.locator("#connect").click();
  await popup.locator("#status").getByText("Connecting to OpenCore").waitFor();
  await popup.close();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:8989/");
  console.log(JSON.stringify({ extensionWorker: worker.url(), testUrl: page.url(), paired: true }));
  await new Promise((accept) => process.once("SIGINT", accept));
  console.log(JSON.stringify({ result: await page.locator("#result").textContent(), field: await page.locator("#field").inputValue() }));
} finally {
  await context?.close();
  await new Promise((accept) => server.close(accept));
  const safeRoot = resolve(tmpdir()) + sep;
  if (!resolve(profile).startsWith(safeRoot) || !basename(profile).startsWith("opencore-chrome-smoke-")) {
    throw new Error("Refusing to remove a profile outside the temporary smoke directory");
  }
  await rm(profile, { recursive: true, force: true });
}
