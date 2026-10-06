import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright";

const css = (await Promise.all(["appearance.css", "styles.css", "WorkspacePanel.css"]
  .map(name => readFile(new URL(`../src/${name}`, import.meta.url), "utf8"))))
  .join("\n").replace(/@import\s+[^;]+;/g, "");

async function edgePixels(page, bounds, scale, inset) {
  const screenshot = (await page.screenshot()).toString("base64");
  return page.evaluate(async ({ screenshot, bounds, scale, inset }) => {
    const image = new Image();
    image.src = `data:image/png;base64,${screenshot}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const { left, top, right, bottom } = bounds;
    return [
      [(left + right) / 2, top + inset],
      [right - inset, (top + bottom) / 2],
      [(left + right) / 2, bottom - inset],
      [left + inset, (top + bottom) / 2],
    ].map(([x, y]) => Array.from(context.getImageData(Math.floor(x * scale), Math.floor(y * scale), 1, 1).data).slice(0, 3));
  }, { screenshot, bounds, scale, inset });
}

function assertStroke(pixels, color, label) {
  const expected = color.match(/\d+/g).slice(0, 3).map(Number);
  for (let edge = 0; edge < pixels.length; edge++) {
    assert.ok(pixels[edge].every((value, channel) => Math.abs(value - expected[channel]) <= 2),
      `${label}: edge ${edge} pixel ${pixels[edge]} must match the uniform stroke ${expected}`);
  }
}

test("capture and native activity frames paint all four app edges across display scales", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const scale of [1, 1.25, 1.5, 2]) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: scale });
      try {
        const page = await context.newPage();
        for (const [width, height] of [[960, 540], [480, 640]]) {
          const image = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#dde6f4"/></svg>`)}`;
          for (const mode of ["fit", "actual"]) {
            const size = mode === "fit" ? "620px;height:560px" : "1040px;height:860px";
            await page.setContent(`<style>${css}</style><div style="margin:24px;display:flex;width:${size}"><div class="desktop-stage desktop-stage-${mode}"><div class="desktop-screen"><img src="${image}" width="${width}" height="${height}" alt="Synthetic app capture"/></div></div></div>`);
            await page.locator("img").evaluate(image => image.decode());
            const frame = await page.locator("img").evaluate(image => {
              const rect = image.getBoundingClientRect();
              const style = getComputedStyle(image);
              const wrapper = getComputedStyle(image.parentElement);
              return {
                bounds: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
                width: rect.width, height: rect.height,
                color: style.outlineColor, stroke: parseFloat(style.outlineWidth), inset: parseFloat(style.outlineOffset),
                wrapperBorder: [wrapper.borderTopWidth, wrapper.borderRightWidth, wrapper.borderBottomWidth, wrapper.borderLeftWidth].map(parseFloat),
              };
            });
            const label = `${width}x${height} ${mode} DPR ${scale}`;
            assert.deepEqual(frame.wrapperBorder, [0, 0, 0, 0], `${label}: letterbox canvas must not receive the app frame`);
            assert.ok(frame.stroke > 0 && frame.inset === -frame.stroke, `${label}: image frame must stay inside the image without changing click geometry`);
            assert.ok(Math.abs(frame.width / frame.height - width / height) < 0.001, `${label}: capture aspect ratio`);
            if (mode === "actual") assert.deepEqual([frame.width, frame.height], [width, height], `${label}: native capture dimensions`);
            assertStroke(await edgePixels(page, frame.bounds, scale, frame.stroke / 2), frame.color, label);
          }
        }
        await page.setContent(`<style>${css}</style><div class="desktop-activity-frame" aria-hidden="true"></div>`);
        const activity = await page.locator(".desktop-activity-frame").evaluate(frame => {
          const rect = frame.getBoundingClientRect();
          const style = getComputedStyle(frame);
          return {
            bounds: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
            color: style.borderTopColor,
            widths: [style.borderTopWidth, style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth].map(parseFloat),
          };
        });
        assert.ok(activity.widths[0] > 0 && activity.widths.every(width => width === activity.widths[0]), `activity DPR ${scale}: equal physical edge widths`);
        assertStroke(await edgePixels(page, activity.bounds, scale, activity.widths[0] / 2), activity.color, `activity DPR ${scale}`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
});
