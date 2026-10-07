import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { chromium } from "playwright";

const production = (await Promise.all(["appearance.css", "styles.css", "WorkspacePanel.css", "agent-platform.css"]
  .map(name => readFile(new URL(`../src/${name}`, import.meta.url), "utf8"))))
  .join("\n").replace(/@import\s+[^;]+;/g, "");
const restored = await readFile(new URL("../src/historic-dark.css", import.meta.url), "utf8");
const svg = '<svg width="14" height="14" viewBox="0 0 24 24"><path d="M3 3L21 21"/></svg>';
const markup = `<div class="conversation-list-focus" style="width:300px;height:500px">
  <div class="conversation-list-head"><h2>Conversations</h2></div>
  <div class="conversation-sections"><button class="active">All</button><button>Hermes</button></div>
</div><div class="assistant-thread-panel chat-mode" style="width:800px;height:600px">
  <div class="assistant-response">
    <details class="assistant-disclosure kind-thinking"><summary>${svg}<strong>Reasoned</strong><span>Read the project</span></summary></details>
    <details class="tool-group" open><summary>${svg}<strong>Used 2 tools</strong><span>Read files</span><em>Show</em></summary>
      <ol class="tool-chain"><li><details class="tool-activity"><summary>${svg}<strong>Read files</strong><span>Complete</span>${svg.replace('<svg ', '<svg class="tool-status-icon" ')}</summary></details></li></ol>
    </details>
    <details class="tool-activity failed"><summary>${svg}<strong>Failed</strong><span>Try again</span></summary></details>
    <div class="aui-md"><p><strong>Important text</strong> and <a href="https://example.com">model card</a>.</p></div>
  </div>
  <div class="chat-composer"><textarea>Message</textarea></div>
  <div class="composer-popover approval-popover" style="position:relative;inset:auto;transform:none">
    <div class="approval-rail"><div class="approval-track"><button class="selected">Ask</button><button>Approve</button><button>Chat</button><button>Allow</button></div>
    <span class="approval-thumb"></span><input class="approval-range" type="range" min="0" max="3" value="0" aria-label="Approval level"/></div>
    <div class="approval-labels"><span>Ask every time</span><span>Approve for me</span><span>Allow everything in this chat</span><span>Allow everything</span></div>
  </div>
</div>`;
const selectors = [".conversation-list-focus", ".conversation-list-head", ".conversation-sections button",
  ".assistant-thread-panel", ".kind-thinking", ".kind-thinking summary svg", ".kind-thinking summary strong",
  ".tool-group", ".tool-group > summary svg", ".tool-group > summary strong", ".tool-group > summary em",
  ".tool-chain .tool-activity", ".tool-chain .tool-activity svg", ".tool-status-icon", ".tool-activity.failed",
  ".aui-md strong", ".aui-md a", ".chat-composer", ".composer-popover", ".approval-thumb", ".approval-range", ".approval-labels"];
const geometryProperties = ["display", "position", "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
  "padding", "margin", "gap", "fontSize", "lineHeight", "overflow", "zIndex", "pointerEvents", "transform",
  "gridTemplateColumns", "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth"];
const palettes = {
  dark: "--bg:#08121b;--surface:#0c1823;--surface-2:#101f2c;--surface-3:#142536;--text:#e3edf7;--muted:#abb3c0;--line:#263b4d;--line-soft:#1b2d3c;--green:#35d38a;--amber:#f5ad32;--red:#ff646d",
  light: "--bg:#f4f5f8;--surface:#fff;--surface-2:#f1f2f6;--surface-3:#e9ebf2;--text:#202432;--muted:#596477;--line:#c6cbd6;--line-soft:#e0e3ea;--green:#116348;--amber:#87531a;--red:#a72337",
};

async function inspect(page) {
  return page.evaluate(({ selectors, geometryProperties }) => Object.fromEntries(selectors.map(selector => {
    const element = document.querySelector(selector);
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return [selector, {
      paint: Object.fromEntries(Array.from(style, name => [name, style.getPropertyValue(name)])),
      geometry: { ...Object.fromEntries(geometryProperties.map(name => [name, style[name]])),
        bounds: [rect.x, rect.y, rect.width, rect.height] },
    }];
  })), { selectors, geometryProperties });
}

test("historical dark paint restores real gradients without changing light theme or control layout", async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.OPENCORE_UI_TEST_BROWSER ? { channel: process.env.OPENCORE_UI_TEST_BROWSER } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    for (const theme of ["light", "contrast", "dark"]) {
      const contrast = theme === "contrast";
      const selectedTheme = contrast ? "dark" : theme;
      await page.setContent(`<html data-platform-theme="${selectedTheme}" data-platform-high-contrast="${contrast}" style="--platform-accent:#245ca8;--platform-link:#3d8fe9;--platform-font-family:'Segoe UI',sans-serif;--platform-font-size:14px;${palettes[selectedTheme]}"><head><style>${production}</style></head><body>${markup}</body></html>`);
      const before = await inspect(page);
      await page.addStyleTag({ content: restored });
      const after = await inspect(page);
      if (theme !== "dark") {
        assert.deepEqual(after, before, "dark restoration must leave every sampled light-theme and high-contrast property unchanged");
        continue;
      }
      for (const selector of selectors) assert.deepEqual(after[selector].geometry, before[selector].geometry,
        `${selector}: restoring paint must preserve modern layout and hit targets`);
      const value = (selector, property) => after[selector].paint[property];
      assert.equal(value(".kind-thinking", "background-image"), "linear-gradient(140deg, rgb(20, 27, 42), rgb(16, 25, 35))");
      assert.equal(value(".kind-thinking", "border-top-color"), "rgb(57, 67, 92)");
      assert.equal(value(".kind-thinking summary svg", "color"), "rgb(167, 140, 233)");
      assert.equal(value(".tool-group", "background-color"), "rgb(9, 23, 34)");
      assert.equal(value(".tool-group", "background-image"), "none");
      assert.equal(value(".tool-group", "border-top-color"), "rgb(41, 71, 94)");
      for (const selector of [".tool-group > summary svg", ".tool-chain .tool-activity svg"])
        assert.equal(value(selector, "color"), "rgb(97, 177, 255)");
      for (const selector of [".kind-thinking summary strong", ".tool-group > summary strong"])
        assert.equal(value(selector, "color"), "rgb(227, 237, 247)");
      assert.equal(value(".tool-status-icon", "color"), "rgb(53, 211, 138)");
      assert.equal(value(".aui-md a", "text-decoration-line"), "none");
      assert.equal(value(".aui-md a", "color"), before[".aui-md a"].paint.color);
      assert.equal(value(".aui-md strong", "background-color"), before[".aui-md strong"].paint["background-color"]);
      assert.equal(value(".conversation-list-focus", "background-image"), "linear-gradient(155deg, rgb(25, 28, 34) 0%, rgb(16, 18, 23) 32%, rgb(9, 11, 15) 100%)");
      assert.match(value(".assistant-thread-panel", "background-image"), /rgb\(42, 46, 54\).*rgb\(17, 19, 24\).*rgb\(9, 11, 15\)/);
    }
  } finally { await browser.close(); }
});
