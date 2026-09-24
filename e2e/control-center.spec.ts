import { expect, test } from "@playwright/test";

const screenshotRoot = "C:/Users/hertz/AppData/Local/Temp/opencore-control-center-qa";

test("conversation observability and runtime log surfaces", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) problems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto("/");
  await expect(page).toHaveTitle("OpenCore");
  await expect(page.getByRole("heading", { name: "Conversations" })).toBeVisible();
  await expect(page.locator(".aui-assistant-message")).toHaveCount(1);
  const response = page.locator(".aui-assistant-message").first();
  await expect(response.getByText("Reasoned", { exact: true }).first()).toBeVisible();
  await expect(response.locator(".kind-thinking")).toHaveCount(2);
  await expect(response.locator(".assistant-response > :nth-child(1)")).toHaveClass(/kind-thinking/);
  await expect(response.locator(".assistant-response > :nth-child(4)")).toHaveClass(/kind-thinking/);
  await expect(response.locator(".assistant-progress")).toHaveText("I'll load the CSV and check its columns before writing the chart.");
  await expect(response.locator(".assistant-progress")).toHaveCount(1);
  await expect(response.locator(".kind-thinking").last()).not.toHaveAttribute("open");
  await expect(response.getByText("The analysis script is complete.", { exact: false })).toBeVisible();
  const command = page.locator(".tool-activity").first();
  await expect(command.getByText("Ran a command")).toBeVisible();
  await command.locator("summary").click();
  await expect(command.locator("pre")).toContainText("import pandas as pd");
  const echoReceipt = page.locator(".echo-storage-card").first();
  await expect(response.locator(".echo-storage-card")).toBeVisible();
  await expect(echoReceipt.getByText("ECHO memory")).toBeVisible();
  await echoReceipt.locator("summary").click();
  await expect(echoReceipt).toContainText("Stored working note project/sales_analysis and linked the generated artifact.");
  await page.screenshot({ path: `${screenshotRoot}/conversations-1600x1000.png`, fullPage: false });

  await page.getByRole("button", { name: "Runtime & Logs" }).click();
  await expect(page.getByRole("heading", { name: "Runtime & Logs", level: 1 })).toBeVisible();
  await expect(page.getByText("Runtime Topology", { exact: true })).toBeVisible();
  await expect(page.getByText("Process Supervision", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Error", exact: true }).click();
  await expect(page.locator(".logs-toolbar button.active")).toHaveText("Error");
  await page.screenshot({ path: `${screenshotRoot}/runtime-logs-1600x1000.png`, fullPage: false });
  expect(problems).toEqual([]);
});

test("compact viewport preserves the primary conversation workflow", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Conversations" })).toBeVisible();
  await expect(page.getByText("Build a data analysis script", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: `${screenshotRoot}/conversations-1100x800.png`, fullPage: false });
});

test("composer text sits centered beside its controls", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/");
  const composer = page.locator(".chat-composer");
  const input = page.getByRole("textbox", { name: "Message OpenCore" });
  await input.fill("Open the exits");
  const composerBox = await composer.boundingBox();
  const inputBox = await input.boundingBox();
  const buttonBox = await page.getByRole("button", { name: "Send message" }).boundingBox();
  expect(composerBox && inputBox && buttonBox).toBeTruthy();
  expect(Math.abs((inputBox!.y + inputBox!.height / 2) - (buttonBox!.y + buttonBox!.height / 2))).toBeLessThan(2);
  const textInsets = await input.evaluate((element) => {
    const style = getComputedStyle(element);
    return [parseFloat(style.paddingTop), parseFloat(style.paddingBottom)];
  });
  expect(Math.abs(textInsets[0] - textInsets[1])).toBeLessThan(1);
  await composer.screenshot({ path: `${screenshotRoot}/composer-centered.png` });
});

test("only active reasoning stays open and prior reasoning reads Reasoned", async ({ page }) => {
  await page.goto("/?previewReasoning=1");
  const reasoning = page.locator(".aui-assistant-message .kind-thinking");
  await expect(reasoning).toHaveCount(2);
  await expect(reasoning.first()).not.toHaveAttribute("open");
  await expect(reasoning.first().locator("summary strong")).toHaveText("Reasoned");
  await expect(reasoning.last()).toHaveAttribute("open");
  await expect(reasoning.last().locator("summary strong")).toHaveText("Reasoning");
  await expect(reasoning.last().locator(".reasoning-text")).toBeVisible();
  await expect(page.locator(".assistant-progress")).toContainText("I'll load the CSV");
});

test("effort and approval remain usable while a response is active", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.goto("/?previewActive=1");
  await expect(page.getByRole("button", { name: "Stop generation" })).toBeVisible({ timeout: 5000 });
  await page.getByRole("button", { name: /Effort:/ }).click();
  await expect(page.getByRole("region", { name: "Effort settings" })).toBeVisible();
  await page.waitForTimeout(2200);
  await expect(page.getByRole("region", { name: "Effort settings" })).toBeVisible();
  await page.getByRole("button", { name: /Approval:/ }).click();
  await expect(page.getByRole("region", { name: "Approval settings" })).toBeVisible();
  await page.getByRole("button", { name: /Effort:/ }).click();
  await expect(page.getByRole("region", { name: "Effort settings" })).toBeVisible();
  await page.getByRole("button", { name: "OpenCore Browser", exact: true }).click();
  await page.getByRole("button", { name: "Split browser" }).click();
  await page.getByRole("button", { name: /Effort:/ }).click();
  await expect(page.getByRole("region", { name: "Effort settings" })).toBeVisible();
  const effortBox = await page.getByRole("region", { name: "Effort settings" }).boundingBox();
  const browserBox = await page.locator(".workspace-browser").boundingBox();
  expect(effortBox && browserBox && effortBox.x + effortBox.width <= browserBox.x + 1).toBeTruthy();
  const stopColor = await page.getByRole("button", { name: "Stop generation" }).evaluate((button) => getComputedStyle(button).backgroundColor);
  expect(stopColor).toBe("rgb(179, 38, 45)");
});

test("browser and computer panel layouts survive close and reload", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 950 });
  await page.goto("/");
  await page.getByRole("button", { name: "OpenCore Browser", exact: true }).click();
  await page.getByRole("button", { name: "Split browser" }).click();
  await page.getByRole("button", { name: "Close browser" }).click();
  await page.getByRole("button", { name: "OpenCore Browser", exact: true }).click();
  await expect(page.getByRole("button", { name: "Expand browser" })).toBeVisible();
  await page.getByRole("button", { name: "Close browser" }).click();
  await page.reload();
  await page.getByRole("button", { name: "OpenCore Browser", exact: true }).click();
  await expect(page.getByRole("button", { name: "Expand browser" })).toBeVisible();
  await page.getByRole("button", { name: "Close browser" }).click();

  await page.getByRole("button", { name: "Computer use" }).click();
  await page.getByRole("button", { name: "Restore Desktop" }).click();
  const desktop = page.getByRole("region", { name: "Windows desktop" });
  const header = await page.getByLabel("Move Desktop panel").boundingBox();
  expect(header).not.toBeNull();
  await page.mouse.move(header!.x + 110, header!.y + 24);
  await page.mouse.down();
  await page.mouse.move(header!.x + 165, header!.y + 49, { steps: 5 });
  await page.mouse.up();
  const resize = await page.getByRole("separator", { name: "Resize Desktop se" }).boundingBox();
  expect(resize).not.toBeNull();
  await page.mouse.move(resize!.x + resize!.width / 2, resize!.y + resize!.height / 2);
  await page.mouse.down();
  await page.mouse.move(resize!.x + resize!.width / 2 + 35, resize!.y + resize!.height / 2 + 25, { steps: 5 });
  await page.mouse.up();
  const before = await desktop.boundingBox();
  expect(before).not.toBeNull();
  await page.getByRole("button", { name: "Close Desktop" }).click();
  await page.getByRole("button", { name: "Computer use" }).click();
  await expect(page.getByRole("button", { name: "Maximize Desktop" })).toBeVisible();
  const reopened = await desktop.boundingBox();
  expect(reopened?.width).toBe(before?.width);
  expect(reopened?.x).toBe(before?.x);
  await page.getByRole("button", { name: "Close Desktop" }).click();
  await page.reload();
  await page.getByRole("button", { name: "Computer use" }).click();
  await expect(page.getByRole("button", { name: "Maximize Desktop" })).toBeVisible();
  const reloaded = await desktop.boundingBox();
  expect(reloaded?.width).toBe(before?.width);
  expect(reloaded?.x).toBe(before?.x);
});

test("custom OpenAI-compatible connector can be created in the UI", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Connectors" }).click();
  await expect(page.getByText("vLLM", { exact: true })).toBeVisible();
  await expect(page.getByText("LocalAI", { exact: true })).toBeVisible();
  await page.getByLabel("Name").fill("My Provider");
  await page.getByLabel("Endpoint").fill("http://127.0.0.1:9000/v1");
  await page.getByLabel("Client match").fill("my-provider");
  await page.getByRole("button", { name: "Add connector" }).click();
  await expect(page.getByText(/Saved My Provider/)).toBeVisible();
});
