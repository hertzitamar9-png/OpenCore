import { expect, test } from '@playwright/test';

for (const viewport of [{ width: 1100, height: 760 }, { width: 1920, height: 1080 }]) {
  test(`picker hover follows the pointer and workspace tabs have four borders at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await page.getByRole('button', { name: 'Models', exact: true }).click();
    await page.getByRole('textbox', { name: 'Search models' }).fill('DuoCore');
    const select = page.getByRole('combobox', { name: 'Memory mode for DuoCore', exact: true });
    await select.click();
    const echo = select.getByRole('option', { name: 'ECHO', exact: true });
    const native = select.getByRole('option', { name: 'Native', exact: true });
    const hover = async (option: typeof echo) => {
      const rect = (await option.boundingBox())!;
      await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
      await expect.poll(() => option.evaluate(element => element.matches(':hover'))).toBe(true);
      return option.evaluate(element => getComputedStyle(element).boxShadow);
    };
    expect(await hover(echo)).not.toBe('none');
    const nativeStroke = await hover(native);
    expect(nativeStroke).not.toBe('none');
    expect(await echo.evaluate(element => element.matches(':hover'))).toBe(false);
    expect(await echo.evaluate(element => getComputedStyle(element).boxShadow)).toBe('none');
    await select.press('Escape');
    await expect(select).toHaveValue('echo');
    await page.getByRole('button', { name: 'Workspace', exact: true }).click();
    for (const name of ['Files', 'Browser', 'Computer', 'Side chat']) {
      const tab = page.getByRole('tab', { name, exact: true });
      await tab.click();
      const stroke = await tab.evaluate(element => {
        const style = getComputedStyle(element);
        return ['Top', 'Right', 'Bottom', 'Left'].map(edge => ({
          width: style.getPropertyValue(`border-${edge.toLowerCase()}-width`),
          color: style.getPropertyValue(`border-${edge.toLowerCase()}-color`),
        }));
      });
      expect(stroke.every(edge => edge.width === '1px' && edge.color === stroke[0].color && edge.color !== 'rgba(0, 0, 0, 0)')).toBe(true);
    }
  });
}

test('live resizing scales text and controls, keeps preferences and fills Jobs', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Jobs', exact: true }).click();
  const measure = () => page.evaluate(() => {
    const jobs = document.querySelector<HTMLElement>('.background-jobs')!;
    const button = document.querySelector<HTMLElement>('.jobs-toolbar-actions button')!;
    return { font: parseFloat(getComputedStyle(jobs).fontSize), button: button.getBoundingClientRect().height,
      width: jobs.getBoundingClientRect().width, main: document.querySelector('.section-main')!.getBoundingClientRect().width,
      preference: document.querySelector<HTMLElement>('.app-window-frame')!.style.getPropertyValue('--chat-font-size') };
  });
  const before = await measure();
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect.poll(async () => (await measure()).font).toBeGreaterThan(before.font);
  const after = await measure();
  expect(after.button).toBeGreaterThan(before.button);
  expect(after.preference).toBe(before.preference);
  expect(after.width).toBeGreaterThan(1600);
  expect(after.width).toBeCloseTo(after.main, 0);
  const controls = await page.locator('.window-controls button').evaluateAll(elements => elements.map(e => ({ width: e.getBoundingClientRect().width, height: e.getBoundingClientRect().height })));
  expect(controls).toHaveLength(3);
  expect(controls[1]).toEqual(controls[0]);
  expect(controls[2]).toEqual(controls[0]);
});

test('a short Computer tab can scroll its lower input above the footer', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 450 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Workspace layout settings', exact: true }).click();
  await page.getByRole('tab', { name: 'Computer', exact: true }).click();
  const panel = page.getByRole('tabpanel', { name: 'Computer', exact: true });
  await panel.press('End');
  await expect.poll(async () => page.locator('.desktop-inputbar').evaluate(element => element.getBoundingClientRect().bottom)).toBeLessThanOrEqual(415);
  const geometry = await panel.evaluate(element => ({ bottom: element.getBoundingClientRect().bottom,
    inputBottom: element.querySelector('.desktop-inputbar')!.getBoundingClientRect().bottom, scrollTop: element.scrollTop }));
  expect(geometry.scrollTop).toBeGreaterThan(0);
  expect(geometry.inputBottom).toBeLessThanOrEqual(geometry.bottom + 1);
});

test('generation presets use the themed picker and retain keyboard selection', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Jobs', exact: true }).click();
  await page.getByRole('button', { name: 'New job', exact: true }).click();
  const schedule = page.getByRole('combobox', { name: 'Schedule', exact: true });
  expect(await schedule.evaluate(element => getComputedStyle(element).appearance)).toBe('base-select');
  expect(await schedule.evaluate(element => getComputedStyle(element, '::picker(select)').backgroundImage)).toContain('linear-gradient');
  await schedule.press('Space');
  await page.getByRole('option', { name: 'Fixed interval', exact: true }).press('Enter');
  await expect(schedule).toHaveValue('interval');
  await expect(page.getByLabel('Every (seconds)', { exact: true })).toBeVisible();
});

test('Learning Studio reflows into its column when the workspace is widened', async ({ page }) => {
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Learning Studio', exact: true }).click();
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('separator', { name: 'Resize workspace', exact: true }).press('End');
  const body = page.locator('.learning-body');
  const geometry = await body.evaluate(element => ({ width: element.clientWidth, scrollWidth: element.scrollWidth,
    right: element.getBoundingClientRect().right, actionRight: element.querySelector('.learning-goal-input > button')!.getBoundingClientRect().right }));
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width + 1);
  expect(geometry.actionRight).toBeLessThanOrEqual(geometry.right + 1);
  await page.getByRole('tab', { name: 'Configuration', exact: true }).click();
  expect(await body.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
});

test('fitting an intrinsic 1920px computer capture preserves its lower toolbar', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/e2e/fixtures/workspace-layout.html');
  const geometry = await page.locator('.desktop-screen img').evaluate(image => {
    const panel = image.closest('.desktop-panel')!;
    const input = panel.querySelector('.desktop-inputbar')!;
    return { imageBottom: image.getBoundingClientRect().bottom, imageWidth: image.getBoundingClientRect().width,
      stageBottom: panel.querySelector('.desktop-stage')!.getBoundingClientRect().bottom,
      inputBottom: input.getBoundingClientRect().bottom, panelBottom: panel.getBoundingClientRect().bottom };
  });
  expect(geometry.imageWidth).toBeLessThan(500);
  expect(geometry.imageBottom).toBeLessThanOrEqual(geometry.stageBottom + 1);
  expect(geometry.inputBottom).toBeLessThanOrEqual(geometry.panelBottom + 1);
});

for (const viewport of [{ width: 1180, height: 720 }, { width: 900, height: 450 }]) {
  test(`side chat keeps its composer reachable with maximum text size at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/e2e/fixtures/side-chat-layout.html');
    const panel = page.locator('.unified-workspace-content');
    await panel.press('End');
    await expect.poll(async () => panel.evaluate(element => element.querySelector('.chat-composer-wrap')!.getBoundingClientRect().bottom - element.getBoundingClientRect().bottom)).toBeLessThanOrEqual(1);
    const sizes = await page.getByRole('textbox', { name: 'Message side chat', exact: true }).evaluate(element => {
      const style = getComputedStyle(element);
      return { height: element.getBoundingClientRect().height, lineHeight: parseFloat(style.lineHeight), font: parseFloat(style.fontSize) };
    });
    expect(sizes.font).toBeCloseTo(28.8, 1);
    expect(sizes.height).toBeCloseTo(sizes.lineHeight * 2, 1);
    const layout = await page.locator('.side-chat-context').evaluate(element => ({ contextBottom: element.getBoundingClientRect().bottom,
      composerTop: document.querySelector('.chat-composer-wrap')!.getBoundingClientRect().top }));
    expect(layout.composerTop).toBeGreaterThanOrEqual(layout.contextBottom - 1);
  });
}
