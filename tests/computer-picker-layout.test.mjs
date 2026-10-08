import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright";

const css = (await Promise.all(["appearance.css", "styles.css", "WorkspacePanel.css", "historic-dark.css", "ThemedSelect.css"]
  .map(name => readFile(new URL(`../src/${name}`, import.meta.url), "utf8"))))
  .join("\n").replace(/@import\s+[^;]+;/g, "");

test("long selected app titles stay on one line inside the computer picker", async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.OPENCORE_UI_TEST_BROWSER ? { channel: process.env.OPENCORE_UI_TEST_BROWSER } : {}) });
  try {
    for (const scale of [1, 1.5, 2]) {
      const page = await browser.newPage({ viewport: { width: 480, height: 260 }, deviceScaleFactor: scale });
      try {
        for (const width of [126, 180]) {
          await page.setContent(`<style>${css}
            .desktop-window-picker select { color:#fff;background:#000;border-color:#333; }
            </style><div class="app-window-frame desktop-panel" style="--ui-scale:${scale};width:${width}px;margin:20px">
            <div class="desktop-window-picker"><select class="themed-select" aria-label="Window">
            <button class="themed-select-button" type="button" aria-hidden="true"><selectedcontent></selectedcontent></button>
            <option>OpenCore Native Control Fixture with a long window title</option><option>Another app</option>
            </select></div></div>`);
          const select = page.getByLabel("Window");
          const screenshot = (await select.screenshot()).toString("base64");
          const textHeight = await page.evaluate(async screenshot => {
            const image = new Image(); image.src = `data:image/png;base64,${screenshot}`; await image.decode();
            const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
            const context = canvas.getContext("2d"); context.drawImage(image, 0, 0);
            const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
            const rows = [];
            for (let y = 0; y < height; y++) {
              let pixels = 0;
              // Leave the arrow and border out of the selected title's ink measurement.
              for (let x = 8; x < width * 0.7; x++) {
                const i = (y * width + x) * 4;
                if (data[i] > 180 && data[i + 1] > 180 && data[i + 2] > 180) pixels++;
              }
              if (pixels >= 3) rows.push(y);
            }
            return rows.length ? rows.at(-1) - rows[0] + 1 : 0;
          }, screenshot);
          // A wrapped/clipped second line paints a second band of text within the 31px control.
          assert.ok(textHeight > 0 && textHeight <= Math.ceil(12 * scale * scale),
            `picker ${width}px at scale ${scale}: selected text painted ${textHeight} physical rows`);
          const selected = await select.locator("selectedcontent").boundingBox();
          const picker = await select.boundingBox();
          assert.ok(selected && picker && selected.x + selected.width <= picker.x + picker.width - 18,
            "the selected title must leave room for the dropdown arrow");
          await select.click();
          const option = page.getByRole("option", { name: /OpenCore Native Control Fixture/ });
          const bounds = await option.boundingBox();
          assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 480 && bounds.y + bounds.height <= 260,
            "the open menu must keep the full title within the viewport");
          await page.keyboard.press("Escape");
        }
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
});
