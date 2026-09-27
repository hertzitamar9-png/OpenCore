import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("Windows webview uses HTML file drop events for the composer", async () => {
  const config = JSON.parse(await readFile(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  assert.ok(config.app.windows.length > 0);
  assert.ok(config.app.windows.every((window) => window.dragDropEnabled === false));
});
