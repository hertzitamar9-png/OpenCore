import { expect, test } from '@playwright/test';

for (const viewport of [{ width: 1100, height: 760 }, { width: 1920, height: 1080 }, { width: 2557, height: 1430 }]) {
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
      await expect(tab).toHaveAttribute('aria-selected', 'true');
      await expect.poll(() => tab.evaluate(element => {
        const style = getComputedStyle(element);
        const stroke = ['Top', 'Right', 'Bottom', 'Left'].map(edge => ({
          width: style.getPropertyValue(`border-${edge.toLowerCase()}-width`),
          color: style.getPropertyValue(`border-${edge.toLowerCase()}-color`),
        }));
        return stroke.every(edge => edge.width === '1px' && edge.color === stroke[0].color && edge.color !== 'rgba(0, 0, 0, 0)');
      })).toBe(true);
      const outline = await tab.evaluate(element => {
        const rect = element.getBoundingClientRect();
        const parent = element.parentElement!.getBoundingClientRect();
        const bottomHit = document.elementFromPoint(rect.x + rect.width / 2, rect.bottom - .5);
        return { bottomGap: parent.bottom - rect.bottom, bottomVisible: element.contains(bottomHit) };
      });
      expect(outline.bottomGap).toBeGreaterThanOrEqual(4);
      expect(outline.bottomVisible).toBe(true);
    }
  });

  test(`Files History controls fit without vertical scrolling at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/');
    await page.getByRole('button', { name: 'Workspace', exact: true }).click();
    await page.getByRole('tab', { name: 'Files', exact: true }).click();
    const row = page.getByRole('tablist', { name: 'Files tabs', exact: true });
    const geometry = await row.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return { overflow: element.scrollHeight - element.clientHeight,
        buttons: [...element.querySelectorAll('button')].map(button => {
          const child = button.getBoundingClientRect();
          return { topGap: child.top - rect.top, bottomGap: rect.bottom - child.bottom };
        }) };
    });
    expect(geometry.overflow).toBe(0);
    for (const button of geometry.buttons) {
      expect(button.topGap).toBeGreaterThanOrEqual(3);
      expect(button.bottomGap).toBeGreaterThanOrEqual(3);
    }
    await row.evaluate(element => { element.scrollTop = 100; });
    expect(await row.evaluate(element => element.scrollTop)).toBe(0);
  });
}

test('many web tabs scroll horizontally without clipping their controls', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 760 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('separator', { name: 'Resize workspace', exact: true }).press('Home');
  await page.getByRole('tab', { name: 'Browser', exact: true }).click();
  for (let index = 0; index < 6; index++) await page.getByRole('button', { name: 'New web tab', exact: true }).click();
  const tabs = page.getByRole('tablist', { name: 'Web tabs', exact: true });
  const geometry = await tabs.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { verticalOverflow: element.scrollHeight - element.clientHeight,
      horizontalOverflow: element.scrollWidth - element.clientWidth,
      contained: [...element.querySelectorAll('button')].every(button => {
        const child = button.getBoundingClientRect();
        return child.top >= rect.top + 3 && child.bottom <= rect.bottom - 3;
      }) };
  });
  expect(geometry.verticalOverflow).toBe(0);
  expect(geometry.horizontalOverflow).toBeGreaterThan(0);
  expect(geometry.contained).toBe(true);
  await tabs.evaluate(element => { element.scrollTop = 100; element.scrollLeft = 0; });
  expect(await tabs.evaluate(element => element.scrollTop)).toBe(0);
  await expect(tabs.getByRole('tab', { name: 'Web 1', exact: true })).toBeInViewport();
});

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

test('side chat keeps controls compact and gives a wide composer one message row', async ({ page }) => {
  await page.setViewportSize({ width: 1369, height: 900 });
  await page.goto('/tests/fixtures/workspace-preview.html');
  await page.getByRole('button', { name: 'Workspace', exact: true }).click();
  await page.getByRole('tab', { name: 'Side chat', exact: true }).click();
  await page.getByRole('button', { name: 'Create side chat', exact: true }).click();
  const resize = page.getByRole('separator', { name: 'Resize workspace', exact: true });
  const composer = page.locator('.side-chat-thread .chat-composer');
  const measure = () => composer.evaluate(element => ({
    width: element.clientWidth, scrollWidth: element.scrollWidth,
    box: element.getBoundingClientRect().toJSON(),
    input: element.querySelector('textarea')!.getBoundingClientRect().toJSON(),
    controls: element.querySelector('.composer-controls')!.getBoundingClientRect().toJSON(),
    buttons: [...element.querySelectorAll('.composer-control-button')].map(button => button.getBoundingClientRect().toJSON()),
    send: element.querySelector('.send-button')!.getBoundingClientRect().toJSON(),
  }));
  for (const wide of [true, false, true]) {
    await resize.press(wide ? 'End' : 'Home');
    const layout = await measure();
    expect(layout.buttons).toHaveLength(2);
    for (const button of layout.buttons) {
      expect(button.width).toBeGreaterThan(44);
      expect(button.width).toBeLessThan(180);
      expect(button.right).toBeLessThanOrEqual(layout.send.left);
    }
    expect(layout.send.left - layout.controls.right).toBeLessThan(10);
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1);
    if (wide) {
      expect(layout.input.width).toBeGreaterThan(layout.box.width / 2);
      expect(layout.input.top).toBeLessThan(layout.send.bottom);
      expect(layout.send.top).toBeLessThan(layout.input.bottom);
      expect(layout.input.right).toBeLessThan(layout.controls.left);
    } else {
      expect(layout.input.bottom).toBeLessThanOrEqual(layout.controls.top);
    }
  }
  const input = composer.getByRole('textbox', { name: 'Message side chat', exact: true });
  await input.fill('A message stays intact while resizing.');
  await resize.press('Home');
  await expect(input).toHaveValue('A message stays intact while resizing.');
  await composer.getByRole('button', { name: 'Effort: Off', exact: true }).click();
  await page.getByRole('slider', { name: 'Reasoning effort', exact: true }).press('End');
  await expect(composer.getByRole('button', { name: 'Effort: OpenCore', exact: true })).toBeVisible();
  await expect(composer.getByRole('button', { name: 'Approval: Ask every time', exact: true })).toBeVisible();
});

test('Learning Studio gives the message history space above its compact composer', async ({ page }) => {
  await page.goto('/tests/fixtures/workspace-preview.html');
  await page.getByRole('button', { name: 'Learning Studio', exact: true }).click();
  const thread = page.locator('.learning-assistant-chat .side-chat-thread');
  await expect(thread.getByRole('textbox', { name: 'Message side chat', exact: true })).toBeVisible();
  for (const viewport of [{ width: 1369, height: 900 }, { width: 760, height: 720 }]) {
    await page.setViewportSize(viewport);
    const layout = await thread.evaluate(element => ({
      height: element.getBoundingClientRect().height,
      bottom: element.getBoundingClientRect().bottom,
      messages: element.querySelector('.aui-thread-root')!.getBoundingClientRect().toJSON(),
      composer: element.querySelector('.chat-composer-wrap')!.getBoundingClientRect().toJSON(),
    }));
    expect(layout.messages.height).toBeGreaterThan(layout.height / 2);
    expect(layout.composer.height).toBeLessThan(layout.height / 2);
    expect(Math.abs(layout.composer.bottom - layout.bottom)).toBeLessThanOrEqual(1);
  }
});
