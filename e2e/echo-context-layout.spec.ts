import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

for (const width of [260, 410, 820]) {
  test(`live context stays readable inside a ${width}px panel`, async ({ page }) => {
    // The reported bug is container-width dependent, even on a wide desktop.
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.setContent(`<div style="width:${width}px;padding:12px"><div class="echo-context-status">
      <div class="echo-context-meter"><strong>Live model context</strong>
      <span class="echo-context-reading">3,000,000,000,000 tokens processed in this session · 21,782 / 32,768 in rolling window</span>
      <progress value="21782" max="32768"></progress></div></div></div>`);
    await page.addStyleTag({ content: readFileSync('src/styles.css', 'utf8') });
    const bounds = await page.locator('.echo-context-meter').evaluate(meter => {
      const rect = (element: Element) => {
        const { left, right, top, bottom, width, height } = element.getBoundingClientRect();
        return { left, right, top, bottom, width, height };
      };
      return { meter: rect(meter), title: rect(meter.querySelector('strong')!),
        reading: rect(meter.querySelector('span')!), bar: rect(meter.querySelector('progress')!),
        scrollWidth: meter.scrollWidth, clientWidth: meter.clientWidth };
    });
    expect(bounds.title.width).toBeGreaterThan(100);
    expect(bounds.reading.top).toBeGreaterThanOrEqual(bounds.title.bottom);
    expect(bounds.bar.top).toBeGreaterThanOrEqual(bounds.reading.bottom);
    for (const child of [bounds.title, bounds.reading, bounds.bar]) {
      expect(child.left).toBeGreaterThanOrEqual(bounds.meter.left - 1);
      expect(child.right).toBeLessThanOrEqual(bounds.meter.right + 1);
    }
    expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth + 1);
  });
}
