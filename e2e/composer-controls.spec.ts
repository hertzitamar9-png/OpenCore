import { expect, test } from "@playwright/test";

test.use({ channel: "chrome" });

test("composer controls open above the send row without clipping", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.goto("/");
  const composer = page.locator(".chat-composer");
  const approval = composer.getByRole("button", { name: /Approval:/ });
  const effort = composer.getByRole("button", { name: /Effort:/ });
  await expect(approval).toBeVisible();
  await expect(effort).toBeVisible();
  await expect(page.getByText(/Project tools ready/)).toHaveCount(0);
  await effort.click();
  const effortPanel = page.getByRole("region", { name: "Effort settings" });
  await expect(effortPanel).toBeVisible();
  const panelBox = await effortPanel.boundingBox();
  const composerBox = await composer.boundingBox();
  expect(panelBox && composerBox && panelBox.y + panelBox.height <= composerBox.y).toBeTruthy();
  await page.screenshot({ path: "artifacts/composer-effort-desktop.png" });
  await page.getByRole("slider", { name: "Reasoning effort" }).focus();
  await page.keyboard.press("End");
  await expect(effort).toContainText("OpenCore");
  for (let index = 0; index < 7; index++) {
    await page.keyboard.press(index === 0 ? "Home" : "ArrowRight");
    await expect(page.getByRole("slider", { name: "Reasoning effort" })).toHaveValue(String(index));
    await expect(page.locator(".effort-segments span.lit")).toHaveCount(index + 1);
  }
  await page.screenshot({ path: "artifacts/composer-effort-opencore.png" });
  await approval.click();
  await expect(page.getByRole("group", { name: "Approval mode" })).toBeVisible();
  await expect(effortPanel).toHaveCount(0);
  await page.screenshot({ path: "artifacts/composer-approval-desktop.png" });
});

test("controls stay usable at a narrow app width", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 760 });
  await page.goto("/");
  await page.locator(".chat-composer").getByRole("button", { name: /Approval:/ }).click();
  const panel = page.getByRole("region", { name: "Approval settings" });
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 900).toBeTruthy();
  await page.screenshot({ path: "artifacts/composer-approval-narrow.png" });
});

test("effort and its panel drag with the pointer", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.goto("/");
  await page.locator(".chat-composer").getByRole("button", { name: /Effort:/ }).click();
  const slider = page.getByRole("slider", { name: "Reasoning effort" });
  const stops = page.locator(".effort-segments span");
  const first = await stops.first().boundingBox();
  const last = await stops.last().boundingBox();
  expect(first && last).toBeTruthy();
  await page.mouse.move(first!.x + first!.width / 2, first!.y + first!.height / 2);
  await page.mouse.down();
  await page.mouse.move(last!.x + last!.width / 2, last!.y + last!.height / 2, { steps: 12 });
  await page.mouse.up();
  await expect(slider).toHaveValue("6");
  await expect(slider).toHaveAttribute("aria-valuetext", "OpenCore");

  const panel = page.getByRole("region", { name: "Effort settings" });
  const before = await panel.boundingBox();
  const title = panel.getByLabel("Move Effort panel");
  const header = await title.boundingBox();
  expect(before && header).toBeTruthy();
  await page.mouse.move(header!.x + 100, header!.y + header!.height / 2);
  await page.mouse.down();
  await page.mouse.move(header!.x + 45, header!.y - 40, { steps: 8 });
  await page.mouse.up();
  const after = await panel.boundingBox();
  expect(after && before && after.x < before.x - 35 && after.y < before.y - 25).toBeTruthy();
});

test("long user messages remain inside their bubble", async ({ page }) => {
  await page.setViewportSize({ width: 1180, height: 760 });
  await page.goto("/");
  const bubble = page.locator(".aui-user-bubble").first();
  await expect(bubble).toBeVisible();
  const result = await bubble.evaluate((element) => {
    element.textContent = "C:\\Users\\hertz\\Documents\\MinecraftCreator\\".repeat(25);
    const bounds = element.getBoundingClientRect();
    const panel = element.closest(".aui-thread-root")!.getBoundingClientRect();
    return { scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, right: bounds.right, panelRight: panel.right };
  });
  expect(result.scrollWidth).toBeLessThanOrEqual(result.clientWidth + 1);
  expect(result.right).toBeLessThanOrEqual(result.panelRight + 1);
});

test("composer shows two complete lines and the wheel advances one line", async ({ page }) => {
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Message OpenCore" });
  await input.fill("row one\nrow two\nrow three\nrow four");
  const geometry = await input.evaluate((element: HTMLTextAreaElement) => {
    const style = getComputedStyle(element);
    return { contentHeight: element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom), line: parseFloat(style.lineHeight), scrollHeight: element.scrollHeight, height: element.clientHeight, rows: element.rows };
  });
  expect(geometry.rows).toBe(2);
  expect(Math.abs(geometry.contentHeight - geometry.line * 2)).toBeLessThanOrEqual(2);
  expect(geometry.scrollHeight).toBeGreaterThan(geometry.height);
  await input.evaluate((element: HTMLTextAreaElement) => {
    element.scrollTop = 0;
    element.dispatchEvent(new WheelEvent("wheel", { deltaY: 120, bubbles: true, cancelable: true }));
  });
  const firstScroll = await input.evaluate((element: HTMLTextAreaElement) => element.scrollTop);
  expect(Math.abs(firstScroll - geometry.line)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: "C:/Users/hertz/AppData/Local/Temp/opencore-composer-two-lines.png" });
});
