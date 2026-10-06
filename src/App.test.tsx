import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App, { historySyncProgressLabel, recentPromptProgress } from "./App";
import * as api from "./api";
import opencoreLogo from "./assets/opencore-logo.png";
import type { ArchiveEvent, ArchivePageRef, OperationRecord } from "./types";
import * as dialog from "@tauri-apps/plugin-dialog";
import { installExternalLinkGuard } from "./external-links";
import * as platform from './agent-platform';

const stageClipboardAttachment = vi.hoisted(() => vi.fn());
vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  stageComposerAttachment: stageClipboardAttachment,
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn(async () => "0.2.58") }));
const eventHandlers = vi.hoisted(() => new Map<string, (event: { payload: unknown }) => void>());
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, callback: (event: { payload: unknown }) => void) => {
  eventHandlers.set(name, callback);
  return () => { eventHandlers.delete(name); };
}) }));

function mockFooterModels() {
  const base: api.InstalledModel = { id: "echo", label: "ECHO 3T", description: "Installed chat model", precision: "BF16",
    contextTokens: 262144, license: "Apache", experimental: false, note: "Pinned", selectable: true,
    installed: true, externalManaged: false, downloadBytes: 0, totalBytes: 5e9, category: "text", backend: "gguf" };
  return vi.spyOn(api, "modelLibrary").mockResolvedValue({ models: [base, { ...base, id: "swift-27b", label: "Swift 1.5", precision: "IQ2_S" }],
    progress: null, diskFreeBytes: 140e9, minimumFreeBytes: 64e6 });
}

describe("OpenCore", () => {
  it("switches an idle running model from the footer in order and keeps competing choices paused", async () => {
    const initial = await api.snapshot();
    let runtime = { ...initial.runtime, status: "running", profile: "echo" };
    const snapshot = vi.spyOn(api, "snapshot").mockImplementation(async () => ({ ...initial, runtime, activeConversationIds: [] }));
    const library = mockFooterModels();
    const calls: string[] = [];
    let finishStop!: () => void;
    const stop = vi.spyOn(api, "stopRuntime").mockImplementation(() => {
      calls.push("stop");
      return new Promise(resolve => { finishStop = () => { runtime = { ...runtime, status: "stopped", profile: "stopped" }; resolve(); }; });
    });
    const select = vi.spyOn(api, "selectProfile").mockImplementation(async profile => { calls.push(`select:${profile}`); });
    const start = vi.spyOn(api, "startProfile").mockImplementation(async profile => { calls.push(`start:${profile}`); runtime = { ...runtime, status: "running", profile }; });
    try {
      render(<App />);
      const trigger = await screen.findByRole("button", { name: "Choose model profile, currently ECHO 3T" });
      expect(trigger).toBeEnabled();
      fireEvent.click(trigger);
      const menu = await screen.findByRole("group", { name: "Choose model profile" });
      expect(document.querySelector(".statusbar")).not.toContainElement(menu);
      fireEvent.click(await within(menu).findByRole("button", { name: /^Swift 1\.5/ }));
      expect(calls).toEqual(["stop"]);
      fireEvent.click(trigger);
      const waiting = await screen.findByRole("group", { name: "Choose model profile" });
      expect(await within(waiting).findByRole("button", { name: /^Swift 1\.5/ })).toBeDisabled();
      expect(within(waiting.parentElement!).getByRole("status")).toHaveTextContent("Changing the model…");
      await act(async () => finishStop());
      await screen.findByRole("button", { name: "Choose model profile, currently Swift 1.5 · ECHO" });
      expect(calls).toEqual(["stop", "select:swift-27b", "start:swift-27b"]);
    } finally { snapshot.mockRestore(); library.mockRestore(); stop.mockRestore(); select.mockRestore(); start.mockRestore(); }
  });

  it("opens the footer menu during an active chat and explains why model switching is held", async () => {
    const initial = await api.snapshot();
    const snapshot = vi.spyOn(api, "snapshot").mockResolvedValue({ ...initial, runtime: { ...initial.runtime, status: "running", profile: "echo" }, activeConversationIds: ["preview"] });
    const library = mockFooterModels();
    const stop = vi.spyOn(api, "stopRuntime").mockResolvedValue();
    const start = vi.spyOn(api, "startProfile").mockResolvedValue();
    try {
      render(<App />);
      fireEvent.click(await screen.findByRole("button", { name: "Choose model profile, currently ECHO 3T" }));
      const menu = await screen.findByRole("group", { name: "Choose model profile" });
      const alternate = await within(menu).findByRole("button", { name: /^Swift 1\.5/ });
      expect(alternate).toBeDisabled();
      expect(screen.getByText("Wait for the active chat to finish before changing models.")).toBeVisible();
      fireEvent.click(alternate);
      expect(stop).not.toHaveBeenCalled();
      expect(start).not.toHaveBeenCalled();
    } finally { snapshot.mockRestore(); library.mockRestore(); stop.mockRestore(); start.mockRestore(); }
  });

  it('keeps the footer inspectable during an app update and prevents competing runtime commands', async () => {
    const initial = await api.snapshot();
    const snapshot = vi.spyOn(api, 'snapshot').mockResolvedValue({ ...initial, runtime: { ...initial.runtime, status: 'running', profile: 'echo' }, activeConversationIds: [] });
    const library = mockFooterModels();
    const check = vi.spyOn(api, 'checkLatestAppVersion').mockResolvedValue({ currentVersion: '1.2.0', available: true, version: '1.3.0' });
    let complete!: () => void;
    const install = vi.spyOn(api, 'installLatestAppUpdate').mockImplementation(() => new Promise<void>(resolve => { complete = resolve; }));
    const stop = vi.spyOn(api, 'stopRuntime').mockResolvedValue();
    const start = vi.spyOn(api, 'startProfile').mockResolvedValue();
    try {
      render(<App />);
      fireEvent.click(await screen.findByRole('button', { name: 'Update' }));
      const installButton = await screen.findByRole('button', { name: 'Install update' });
      await waitFor(() => expect(installButton).toBeEnabled());
      fireEvent.click(installButton);
      fireEvent.click(screen.getByRole('button', { name: 'Choose model profile, currently ECHO 3T' }));
      const menu = await screen.findByRole('group', { name: 'Choose model profile' });
      expect(await within(menu).findByRole('button', { name: /^Swift 1\.5/ })).toBeDisabled();
      expect(within(menu.parentElement!).getByRole('status')).toHaveTextContent('Wait for the app update to finish.');
      expect(stop).not.toHaveBeenCalled();
      expect(start).not.toHaveBeenCalled();
      await act(async () => complete());
      await waitFor(() => expect(within(menu).getByRole('button', { name: /^Swift 1\.5/ })).toBeEnabled());
    } finally { if (complete) await act(async () => complete()); snapshot.mockRestore(); library.mockRestore(); check.mockRestore(); install.mockRestore(); stop.mockRestore(); start.mockRestore(); }
  });

  it('retains the current model when stopping it for a switch fails', async () => {
    const initial = await api.snapshot();
    const snapshot = vi.spyOn(api, 'snapshot').mockResolvedValue({ ...initial, runtime: { ...initial.runtime, status: 'running', profile: 'echo' }, activeConversationIds: [] });
    const library = mockFooterModels();
    const stop = vi.spyOn(api, 'stopRuntime').mockRejectedValue(new Error('Runtime is still busy'));
    const select = vi.spyOn(api, 'selectProfile').mockResolvedValue();
    const start = vi.spyOn(api, 'startProfile').mockResolvedValue();
    try {
      render(<App />);
      fireEvent.click(await screen.findByRole('button', { name: 'Choose model profile, currently ECHO 3T' }));
      const menu = await screen.findByRole('group', { name: 'Choose model profile' });
      fireEvent.click(await within(menu).findByRole('button', { name: /^Swift 1\.5/ }));
      expect(await screen.findByText('Could not change the model: Error: Runtime is still busy')).toBeVisible();
      expect(screen.getByRole('button', { name: 'Choose model profile, currently ECHO 3T' })).toBeEnabled();
      expect(select).not.toHaveBeenCalled();
      expect(start).not.toHaveBeenCalled();
    } finally { snapshot.mockRestore(); library.mockRestore(); stop.mockRestore(); select.mockRestore(); start.mockRestore(); }
  });

  it("reports ECHO conversation progress and calls out stale batch updates accurately", () => {
    const operation: OperationRecord = {
      id: "sync", kind: "history_sync", target: "codex",
      phase: "Indexing exact history in ECHO · conversation 77/120 complete · 518 batches and 4144 events queued",
      status: "running", current: 77, total: 120, imported: 0, updated: 0, skipped: 0,
      summary: "", startedAt: "2026-10-05T10:00:00Z", lastProgressAt: "2026-10-05T10:00:00Z",
    };
    expect(historySyncProgressLabel(operation, Date.parse("2026-10-05T10:00:05Z"))).toContain("updating");
    const stale = historySyncProgressLabel(operation, Date.parse("2026-10-05T10:00:31Z"));
    expect(stale).toContain("77/120 complete");
    expect(stale).toContain("no new batch update for 31s");
    expect(stale).not.toContain("77/120 files");
  });
  it("opens the requested 3D category from a chat handoff link", async () => {
    const initial=await api.snapshot();
    const chat=initial.conversations[0];
    const conversation=vi.spyOn(api,"conversation").mockResolvedValue([{id:998,conversationId:chat.id,timestamp:"2026-10-02T12:00:00Z",kind:"message",role:"assistant",source:"OpenCore",title:"Background job",content:"[Open 3D generation](opencore-studio://3d)",metadata:{}}]);
    try {
      render(<App/>);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getAllByText(chat.title,{selector:"strong"})[0]);
      fireEvent.click(await screen.findByRole("link",{name:"Open 3D generation"}));
      expect(await screen.findByRole("heading",{name:"Game Dev Studio"})).toBeVisible();
      expect(screen.getByRole("button",{name:"3D assets"})).toHaveAttribute("aria-pressed","true");
    } finally {conversation.mockRestore();}
  });
  it("announces a finished studio job while chat remains open", async () => {
    render(<App />);
    await screen.findByLabelText("Message OpenCore");
    await waitFor(() => expect(eventHandlers.has("opencore-studio-job")).toBe(true));
    act(() => eventHandlers.get("opencore-studio-job")?.({payload:{id:"music-finished",category:"music",status:"completed"}}));
    expect(await screen.findByText("Generation complete. Open Music Studio to view the output.")).toBeVisible();
  });
  it("opens the linked local file from an older chat without switching to the newest chat", async () => {
    const initial = await api.snapshot();
    const older = { ...initial.conversations[0], id: "older", title: "Earlier research" };
    const snapshot = vi.spyOn(api, "snapshot").mockResolvedValue({ ...initial, conversations: [initial.conversations[0], older] });
    const path = "C:/project/frozen research.md";
    const conversation = vi.spyOn(api, "conversation").mockResolvedValue([{
      id: 111, conversationId: older.id, timestamp: "2026-09-28T10:00:00Z", kind: "message", role: "assistant",
      source: "OpenCore", title: "Research", content: `[Frozen research record](</${path}:12>) and [Unavailable](relative.md)`, metadata: {},
    }]);
    const preview = vi.spyOn(api, "previewComposerAttachment").mockResolvedValue({ name: "frozen research.md", mime: "text/plain", size: 20, dataUrl: "", text: "Original research source" });
    const disposeLinks = installExternalLinkGuard(vi.fn().mockResolvedValue(undefined));
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getAllByText(older.title, { selector: "strong" })[0]);
      await screen.findByRole("heading", { name: older.title, level: 2 });
      const link = await screen.findByRole("link", { name: "Frozen research record" });
      fireEvent.click(link);
      const browser = await screen.findByRole("complementary", { name: "Workspace" });
      expect(await within(browser).findByText("Original research source")).toBeVisible();
      expect(preview).toHaveBeenCalledWith(path);
      expect(screen.getByRole("heading", { name: older.title, level: 2 })).toBeVisible();
      expect(screen.queryByRole("link", { name: "Unavailable" })).not.toBeInTheDocument();
    } finally { disposeLinks(); snapshot.mockRestore(); conversation.mockRestore(); preview.mockRestore(); }
  });
  it("shows the installed app version rather than a hard-coded release", async () => {
    render(<App />);
    await screen.findByLabelText("Message OpenCore");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(await screen.findByText("OpenCore v0.2.58")).toBeInTheDocument();
  });

  it("opens the bundled Chrome extension folder from Settings", async () => {
    const extensionPath = "C:/Program Files/OpenCore/resources/chrome-extension";
    const status = vi.spyOn(api, "browserBridgeStatus").mockResolvedValue({
      port: 8814, token: "pairing-token", connected: false, extensionPath,
    });
    const open = vi.spyOn(api, "openLocalPath").mockResolvedValue();
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(await screen.findByRole("button", { name: "Open extension folder" }));
      await waitFor(() => expect(open).toHaveBeenCalledWith(extensionPath));
    } finally { status.mockRestore(); open.mockRestore(); }
  });

  it("shows the bundled OpenCore logo while runtime state is still loading", () => {
    const snapshot = vi.spyOn(api, "snapshot").mockImplementation(() => new Promise(() => {}));
    try {
      render(<App />);
      expect(screen.getByText("Loading runtime state…")).toBeInTheDocument();
      expect(screen.getByRole("img", { name: "OpenCore" })).toHaveAttribute("src", opencoreLogo);
    } finally { snapshot.mockRestore(); }
  });

  it("opens model installation from a failed send and preserves the unsent draft", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockRejectedValue(new Error("DuoCore is not installed. Open Models and choose Install."));
    try {
      render(<App />);
      const input = await screen.findByLabelText("Message OpenCore");
      fireEvent.change(input, { target: { value: "Keep this question for DuoCore" } });
      fireEvent.click(screen.getByTitle("Send"));
      const openModels = await screen.findByRole("button", { name: "Open Models" });
      expect(input).toHaveValue("Keep this question for DuoCore");
      fireEvent.click(openModels);
      await screen.findByRole("heading", { name: "Models", level: 1 });
      fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
      expect(await screen.findByLabelText("Message OpenCore")).toHaveValue("Keep this question for DuoCore");
    } finally { send.mockRestore(); }
  });

  it("returns keyboard focus to the draft after an attachment dialog is cancelled or fails", async () => {
    render(<App />);
    const input = await screen.findByLabelText('Message OpenCore');
    fireEvent.change(input, { target: { value: 'Keep my draft' } });
    vi.mocked(dialog.open).mockResolvedValueOnce(null);
    fireEvent.click(screen.getByRole('button', { name: 'Add files or choose model' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload files or images' }));
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveValue('Keep my draft');
    vi.mocked(dialog.open).mockRejectedValueOnce(new Error('Picker failed'));
    fireEvent.click(screen.getByRole('button', { name: 'Add files or choose model' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Upload files or images' }));
    await waitFor(() => expect(input).toHaveFocus());
    fireEvent.change(input, { target: { value: 'Edited' } });
    expect(input).toHaveValue('Edited');
  });
  it("reads the runtime's spaced prompt-progress format", () => {
    const now = Date.now();
    const logs = [
      { id: 1, timestamp: new Date(now - 1000).toISOString(), level: "info", source: "runtime", message: "slot print_timing: prompt processing, n_tokens = 512, progress = 0.50, t = 1.0 s" },
      { id: 2, timestamp: new Date(now).toISOString(), level: "info", source: "runtime", message: "slot print_timing: prompt processing, n_tokens = 1024, progress = 1.00, t = 2.0 s" },
    ];
    expect(recentPromptProgress(logs)).toEqual({ label: "Reading prompt · 1,024 tokens · 100%", speed: 512 });
  });
  beforeEach(() => {
    eventHandlers.clear();
    stageClipboardAttachment.mockReset();
    window.localStorage.removeItem?.("opencore.model-profile");
    window.localStorage.removeItem?.("opencore.reasoning-effort.v1");
    window.localStorage.removeItem?.("opencore.approval-global.v1");
    window.sessionStorage.removeItem?.("opencore.approval-chat.preview");
  });
  it("pastes clipboard images as attachments and sends them with the prompt", async () => {
    stageClipboardAttachment.mockResolvedValueOnce("C:\\temp\\clipboard-image.png");
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      const input = await screen.findByLabelText("Message OpenCore");
      const file = new File(["image bytes"], "clipboard-image.png", { type: "image/png" });
      const transfer = { files: [], items: [{ kind: "file", getAsFile: () => file }], types: ["Files"], getData: () => "" };
      const paste = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(paste, "clipboardData", { value: transfer });
      fireEvent(input, paste);

      expect(paste.defaultPrevented).toBe(true);
      await screen.findByText("clipboard-image.png");
      fireEvent.change(input, { target: { value: "Describe this image" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalled());
      expect(send.mock.calls[0][2]).toEqual(["C:\\temp\\clipboard-image.png"]);
    } finally { send.mockRestore(); }
  });
  it("keeps ordinary text paste in the composer", async () => {
    render(<App />);
    const input = await screen.findByLabelText("Message OpenCore");
    const transfer = { files: [], items: [{ kind: "string", getAsFile: () => null }], types: ["text/plain"], getData: () => "keep this text" };
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", { value: transfer });
    fireEvent(input, paste);

    expect(paste.defaultPrevented).toBe(false);
    expect(stageClipboardAttachment).not.toHaveBeenCalled();
  });
  it("keeps a text paste below the large-paste threshold inline", async () => {
    render(<App />);
    const input = await screen.findByLabelText("Message OpenCore");
    const content = "x".repeat(11_999);
    const transfer = { files: [], items: [{ kind: "string", getAsFile: () => null }], types: ["text/plain"], getData: () => content };
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", { value: transfer });
    fireEvent(input, paste);

    expect(paste.defaultPrevented).toBe(false);
    expect(stageClipboardAttachment).not.toHaveBeenCalled();
  });
  it("turns a text paste at the large-paste threshold into a .txt attachment", async () => {
    stageClipboardAttachment.mockResolvedValueOnce("C:\\temp\\pasted-text.txt");
    render(<App />);
    const input = await screen.findByLabelText("Message OpenCore");
    const content = "x".repeat(12_000);
    const transfer = { files: [], items: [{ kind: "string", getAsFile: () => null }], types: ["text/plain"], getData: () => content };
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(paste, "clipboardData", { value: transfer });
    fireEvent(input, paste);

    expect(paste.defaultPrevented).toBe(true);
    await screen.findByText("pasted-text.txt");
    expect(stageClipboardAttachment).toHaveBeenCalledOnce();
    const stagedFile = stageClipboardAttachment.mock.calls[0][0] as File;
    expect(stagedFile.name).toBe("pasted-text.txt");
    expect(stagedFile.type).toBe("text/plain");
    const stagedText = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(stagedFile);
    });
    expect(stagedText).toBe(content);
  });
  it("shows a drop target and attaches files dropped on the composer", async () => {
    stageClipboardAttachment.mockResolvedValueOnce("C:\\temp\\dropped-notes.txt");
    render(<App />);
    await screen.findByLabelText("Message OpenCore");
    const target = document.querySelector(".chat-composer")!;
    const file = new File(["notes"], "dropped-notes.txt", { type: "text/plain" });
    const dataTransfer = { files: [file], items: [{ kind: "file", getAsFile: () => file }], types: ["Files"] };
    const dragEnter = new Event("dragenter", { bubbles: true, cancelable: true });
    Object.defineProperty(dragEnter, "dataTransfer", { value: dataTransfer });
    fireEvent(target, dragEnter);
    expect(screen.getByText("Drop files to attach")).toBeVisible();

    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", { value: dataTransfer });
    fireEvent(target, drop);
    expect(drop.defaultPrevented).toBe(true);
    await screen.findByText("dropped-notes.txt");
    expect(stageClipboardAttachment).toHaveBeenCalledOnce();
  });

  it("opens a selected text attachment in workspace Files before sending", async () => {
    const path = "C:\\Temp\\notes.txt";
    const picker = vi.mocked(dialog.open).mockResolvedValueOnce(path);
    const preview = vi.spyOn(api, "previewComposerAttachment").mockResolvedValue({ name: "notes.txt", mime: "text/plain", size: 18, dataUrl: "data:text/plain;base64,cHJldmlldyB0ZXh0", text: "preview text" });
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Upload files or images" }));
      await screen.findByText("notes.txt");
      fireEvent.click(screen.getByRole("button", { name: "Preview notes.txt" }));
      expect(await screen.findByRole("complementary", { name: "Workspace" })).toBeInTheDocument();
      expect(await screen.findByText("preview text")).toBeInTheDocument();
      expect(preview).toHaveBeenCalledWith(path);
    } finally { picker.mockReset(); preview.mockRestore(); }
  });

  it("previews attached HTML in workspace Files", async () => {
    const path = "C:\\Temp\\game.html";
    const picker = vi.mocked(dialog.open).mockResolvedValueOnce(path);
    const preview = vi.spyOn(api, "previewComposerAttachment").mockResolvedValue({ name: "game.html", mime: "text/html", size: 16, dataUrl: "data:text/html;base64,PGgxPlBsYXk8L2gxPg==", text: "<h1>Play</h1>" });
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Upload files or images" }));
      await screen.findByText("game.html");
      fireEvent.click(screen.getByRole("button", { name: "Preview game.html" }));
      const browser = await screen.findByRole("complementary", { name: "Workspace" });
      await waitFor(() => expect(browser.querySelector("iframe")).toHaveAttribute("srcdoc", "<h1>Play</h1>"));
      expect(preview).toHaveBeenCalledWith(path);
    } finally { picker.mockReset(); preview.mockRestore(); }
  });
  it("streams answer and reasoning segments live in their actual order", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockImplementation(() => new Promise(() => {}));
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Add walking" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(eventHandlers.has("opencore-generation")).toBe(true));
      act(() => eventHandlers.get("opencore-generation")!({ payload: {
        conversationId: "preview", runId: "live-1", phase: "answering", segments: [
          { kind: "thinking", content: "first live thought" },
          { kind: "text", content: "answer arriving now" },
          { kind: "thinking", content: "latest live thought" },
        ],
      } }));
      const first = await screen.findByText("first live thought");
      const answer = await screen.findByText("answer arriving now");
      const latest = await screen.findByText("latest live thought");
      expect(first.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(answer.compareDocumentPosition(latest) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByRole("button", { name: "Stop generation" })).toBeVisible();
    } finally { send.mockRestore(); }
  });
  it("passes a chosen reasoning mode through the chat request", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      expect(screen.getByRole("button", { name: "Maximize OpenCore" })).toBeVisible();
      expect(screen.queryByRole("button", { name: /Move conversations|Dock conversations/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("slider", { name: "Reasoning effort" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Effort/ }));
      const selector = screen.getByRole("slider", { name: "Reasoning effort" });
      expect(selector).toHaveAttribute("max", "6");
      fireEvent.change(selector, { target: { value: "4" } });
      expect(selector).toHaveAttribute("aria-valuetext", "Extra high");
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Explain this change" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Explain this change", [], "extra-high", "ask-every-time", [], true, 3, true, 200000, expect.any(String)));
    } finally { send.mockRestore(); }
  });

  it("sends zero reasoning budget when Off is selected", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: /Effort/ }));
      const selector = screen.getByRole("slider", { name: "Reasoning effort" });
      fireEvent.change(selector, { target: { value: "0" } });
      expect(selector).toHaveAttribute("aria-valuetext", "Off");
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Answer directly" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Answer directly", [], "off", "ask-every-time", [], true, 3, true, 200000, expect.any(String)));
    } finally { send.mockRestore(); }
  });

  it("defaults to Off for faster replies and keeps Fast mode out of the composer", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      expect(screen.getByRole("button", { name: "Effort: Off" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Fast mode" })).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Reply directly" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Reply directly", [], "off", "ask-every-time", [], true, 3, true, 200000, expect.any(String)));
    } finally { send.mockRestore(); }
  });

  it("keeps effort and approval controls in the send bar and opens one padded panel at a time", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    const composer = document.querySelector(".chat-composer");
    expect(composer).toContainElement(screen.getByRole("button", { name: /Approval/ }));
    expect(composer).toContainElement(screen.getByRole("button", { name: /Effort/ }));
    expect(screen.queryByRole("button", { name: "Fast mode" })).not.toBeInTheDocument();
    expect(composer).toContainElement(screen.getByTitle("Send"));
    expect(screen.queryByText(/Project tools ready/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Read files and search/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Applies to the next message/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Approval/ }));
    expect(screen.getByRole("group", { name: "Approval mode" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve for me" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Effort/ }));
    expect(screen.queryByRole("group", { name: "Approval mode" })).not.toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Reasoning effort" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Maximize Effort" })).not.toBeInTheDocument();
  });

  it("commits a dragged approval level only when the drag ends", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: /Approval/ }));
    const range = screen.getByRole("slider", { name: "Approval level" });
    fireEvent.change(range, { target: { value: "2" } });
    expect(screen.queryByRole("dialog", { name: "Allow everything in this chat?" })).not.toBeInTheDocument();
    fireEvent.pointerUp(range);
    expect(screen.getByRole("dialog", { name: "Allow everything in this chat?" })).toBeInTheDocument();
  });

  it("turns Start into an available Stop while the model is loading", async () => {
    const start = vi.spyOn(api, "startProfile").mockImplementation(() => new Promise(() => {}));
    const stop = vi.spyOn(api, "stopRuntime").mockResolvedValue();
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Runtime & Logs" }));
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
      const stopButton = screen.getByRole("button", { name: "Stop" });
      expect(stopButton).toBeEnabled();
      fireEvent.click(stopButton);
      await waitFor(() => expect(stop).toHaveBeenCalledOnce());
    } finally { start.mockRestore(); stop.mockRestore(); }
  });

  it("turns Send into Stop during a live request", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockImplementation(() => new Promise(() => {}));
    const cancel = vi.spyOn(api, "cancelChatMessage").mockResolvedValue(true);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Please work" } });
      fireEvent.click(screen.getByTitle("Send"));
      fireEvent.click(screen.getByRole("button", { name: "Stop generation" }));
      await waitFor(() => expect(cancel).toHaveBeenCalledWith("preview"));
    } finally { send.mockRestore(); cancel.mockRestore(); }
  });

  it("keeps an unsaved prompt ready when model loading is stopped", async () => {
    let rejectSend: (reason: unknown) => void = () => {};
    const send = vi.spyOn(api, "sendChatMessage").mockImplementation(() => new Promise((_resolve, reject) => { rejectSend = reject; }));
    const cancel = vi.spyOn(api, "cancelChatMessage").mockResolvedValue(true);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Keep this prompt" } });
      fireEvent.click(screen.getByTitle("Send"));
      fireEvent.click(screen.getByRole("button", { name: "Stop generation" }));
      rejectSend("__INTERRUPTED_BEFORE_SAVE__");
      await waitFor(() => expect(screen.getByLabelText("Message OpenCore")).toHaveValue("Keep this prompt"));
    } finally { send.mockRestore(); cancel.mockRestore(); }
  });

  it("dismisses a notice after five seconds", async () => {
    const open = vi.spyOn(api, "openLocalPath").mockRejectedValue(new Error("unavailable"));
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Memory" }));
      vi.useFakeTimers();
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Open folder" })); await Promise.resolve(); });
      expect(screen.getByText(/Could not open folder:.*unavailable/)).toBeInTheDocument();
      act(() => vi.advanceTimersByTime(5000));
      expect(screen.queryByText(/Could not open folder:.*unavailable/)).not.toBeInTheDocument();
    } finally { vi.useRealTimers(); open.mockRestore(); }
  });

  it("shows exact ECHO archive pages behind a scoped search", async () => {
    const overview = vi.spyOn(api, "archiveOverview").mockResolvedValue({ archives: 1, pages: 2, sourceBytes: 120, storedBytes: 80, conversations: [{ conversationId: "preview", pages: 2, sourceBytes: 120, storedBytes: 80, lastTimestamp: 1 }], summaries: [] });
    const search = vi.spyOn(api, "searchArchive").mockResolvedValue([{ archiveFile: "preview.db", pageId: "a".repeat(64), conversationId: "preview", offsetStart: 0, preview: "print('ECHO')" }]);
    const read = vi.spyOn(api, "readArchivePage").mockResolvedValue("```python\nprint('ECHO')\n```");
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Memory" }));
      expect(await screen.findByText("2", { selector: ".memory-summary strong" })).toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Search archived text"), { target: { value: "ECHO" } });
      fireEvent.click(screen.getByRole("button", { name: "Search" }));
      expect(await screen.findByText("print('ECHO')", { selector: ".memory-hit span" })).toBeInTheDocument();
      fireEvent.click(screen.getByText("print('ECHO')", { selector: ".memory-hit span" }));
      expect(await screen.findByText(/print\('ECHO'\)/, { selector: ".memory-page-preview pre" })).toBeInTheDocument();
      expect(search).toHaveBeenCalledWith("ECHO", 75, undefined);
      expect(read).toHaveBeenCalledWith("preview.db", "a".repeat(64));
    } finally { overview.mockRestore(); search.mockRestore(); read.mockRestore(); }
  });

  it("opens recent structured activity in the general ECHO archive", async () => {
    const overview = vi.spyOn(api, "archiveOverview").mockResolvedValue({ archives: 1, pages: 1, sourceBytes: 20, storedBytes: 20, conversations: [{ conversationId: "preview", pages: 1, sourceBytes: 20, storedBytes: 20, lastTimestamp: 1 }], summaries: [], toolCalls: 1, toolResults: 0, imageAssets: 0, fileEvents: 0 });
    const events = vi.spyOn(api, "listArchiveEvents").mockResolvedValue([{ eventId: "a".repeat(64), conversationId: "preview", timestamp: 1, kind: "tool_call", role: "assistant", source: "OpenCore", title: "read_file", content: "src/main.ts", metadata: {}, contentBytes: 11, truncated: false }]);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Memory" }));
      fireEvent.click(screen.getByRole("button", { name: /General ECHO archive/ }));
      expect(await screen.findByText("General ECHO activity")).toBeInTheDocument();
      expect(screen.getByText("src/main.ts")).toBeInTheDocument();
      expect(events).toHaveBeenCalledWith("*", 0, 50);
    } finally { overview.mockRestore(); events.mockRestore(); }
  });

  it("can select an archived chat that is no longer in the conversation list", async () => {
    const overview = vi.spyOn(api, "archiveOverview").mockResolvedValue({ archives: 1, pages: 2, sourceBytes: 84, storedBytes: 70, conversations: [{ conversationId: "orphan-archive", pages: 2, sourceBytes: 84, storedBytes: 70, lastTimestamp: 1 }], summaries: [] });
    const search = vi.spyOn(api, "searchArchive").mockResolvedValue([]);
    const list = vi.spyOn(api, "listArchivePages").mockResolvedValue([{ archiveFile: "orphan.db", pageId: "b".repeat(64), conversationId: "orphan-archive", offsetStart: 0, offsetEnd: 42, timestamp: 1 }, { archiveFile: "orphan.db", pageId: "c".repeat(64), conversationId: "orphan-archive", offsetStart: 42, offsetEnd: 84, timestamp: 2 }]);
    const read = vi.spyOn(api, "readArchivePage").mockImplementation(async (_file, pageId) => pageId.startsWith("b") ? "Exact orphan archive text and code" : "Second exact archived page");
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Memory" }));
      expect(await screen.findByRole("option", { name: "Archived chat · orphan-archive" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /orphan-archive OpenCore 2 pages/ }));
      expect(screen.getByRole("combobox", { name: "Memory scope" })).toHaveValue("chat:orphan-archive");
      expect(await screen.findByText("Exact orphan archive text and code")).toBeInTheDocument();
      expect(await screen.findByText("Second exact archived page")).toBeInTheDocument();
      expect(list).toHaveBeenCalledWith("orphan-archive", 0, 40);
      expect(read).toHaveBeenCalledWith("orphan.db", "b".repeat(64));
      fireEvent.change(screen.getByLabelText("Search archived text"), { target: { value: "some code" } });
      fireEvent.click(screen.getByRole("button", { name: "Search" }));
      await waitFor(() => expect(search).toHaveBeenCalledWith("some code", 75, ["orphan-archive"]));
    } finally { overview.mockRestore(); search.mockRestore(); list.mockRestore(); read.mockRestore(); }
  });

  it("keeps archive controls usable while reading the first pages and ignores closed archive responses", async () => {
    const firstPages: ArchivePageRef[] = Array.from({ length: 40 }, (_, index) => ({
      archiveFile: "first.db", pageId: String(index + 1), conversationId: "first-archive",
      offsetStart: index * 100, offsetEnd: (index + 1) * 100, timestamp: index + 1,
    }));
    const extraPage = { ...firstPages[0], pageId: "41", offsetStart: 4000, offsetEnd: 4100 };
    const secondPage = { ...firstPages[0], archiveFile: "second.db", conversationId: "second-archive" };
    const overview = vi.spyOn(api, "archiveOverview").mockResolvedValue({
      archives: 2, pages: 42, sourceBytes: 4200, storedBytes: 4200, summaries: [],
      conversations: [
        { conversationId: "first-archive", pages: 41, sourceBytes: 4100, storedBytes: 4100, lastTimestamp: 2 },
        { conversationId: "second-archive", pages: 1, sourceBytes: 100, storedBytes: 100, lastTimestamp: 1 },
      ],
    });
    let finishActivity!: (events: ArchiveEvent[]) => void;
    const activity = new Promise<ArchiveEvent[]>(resolve => { finishActivity = resolve; });
    const events = vi.spyOn(api, "listArchiveEvents").mockResolvedValue([]).mockImplementationOnce(() => activity);
    const pages = vi.spyOn(api, "listArchivePages").mockImplementation(async (id, offset) => id === "first-archive" ? offset ? [extraPage] : firstPages : [secondPage]);
    const pending = new Map<string, { promise: Promise<string>; resolve: (text: string) => void }[]>();
    const read = vi.spyOn(api, "readArchivePage").mockImplementation((file, pageId) => {
      if (file === "second.db") return Promise.resolve("Second archive exact text");
      let resolve!: (text: string) => void;
      const promise = new Promise<string>(done => { resolve = done; });
      pending.set(pageId, [...(pending.get(pageId) || []), { promise, resolve }]);
      return promise;
    });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Memory" }));
      await screen.findByRole("option", { name: "Archived chat · first-archive" });
      fireEvent.change(screen.getByRole("combobox", { name: "Memory scope" }), { target: { value: "chat:first-archive" } });
      const reader = await screen.findByRole("region", { name: "Open archive" });
      await within(reader).findByText("Page 40");
      await waitFor(() => expect(read).toHaveBeenCalledTimes(4));
      expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Index activity and files" })).toBeEnabled();
      expect(within(reader).getByRole("button", { name: "Load more pages" })).toBeEnabled();
      expect(within(reader).getByRole("status", { name: "Archive reading status" })).toHaveTextContent("0 read · 4 opening");
      await act(async () => { pending.get("1")![0].resolve("First verified exact page"); });
      expect(within(reader).getByText("First verified exact page")).toBeVisible();
      expect(within(reader).getByRole("status", { name: "Archive reading status" })).toHaveTextContent("1 read · 3 opening");
      fireEvent.click(within(reader).getByRole("button", { name: "Load more pages" }));
      const extra = (await within(reader).findByText("Page 41")).closest("article")!;
      expect(pages).toHaveBeenCalledWith("first-archive", 40, 40);
      expect(within(extra).getByRole("button", { name: "Open page" })).toBeEnabled();
      expect(read).toHaveBeenCalledTimes(4);
      const fifth = within(reader).getByText("Page 5").closest("article")!;
      expect(within(fifth).getByText("Exact text is available on demand. Select Open page to read it.")).toBeVisible();
      fireEvent.click(within(fifth).getByRole("button", { name: "Open page" }));
      expect(read).toHaveBeenCalledTimes(5);
      await act(async () => {
        for (const id of ["2", "3", "4"]) pending.get(id)![0].resolve(`Verified exact page ${id}`);
      });
      expect(within(reader).getByRole("status", { name: "Archive reading status" })).toHaveTextContent("4 read · 1 opening");
      expect(read).toHaveBeenCalledTimes(5);

      fireEvent.change(screen.getByRole("combobox", { name: "Memory scope" }), { target: { value: "chat:second-archive" } });
      await screen.findByText("Second archive exact text");
      fireEvent.click(within(screen.getByRole("region", { name: "Open archive" })).getByRole("button", { name: "Close archive" }));
      expect(screen.queryByRole("region", { name: "Open archive" })).not.toBeInTheDocument();
      fireEvent.change(screen.getByRole("combobox", { name: "Memory scope" }), { target: { value: "chat:first-archive" } });
      const reopened = await screen.findByRole("region", { name: "Open archive" });
      const reopenedFifth = (await within(reopened).findByText("Page 5")).closest("article")!;
      fireEvent.click(within(reopenedFifth).getByRole("button", { name: "Open page" }));
      await waitFor(() => expect(pending.get("5")).toHaveLength(2));
      await act(async () => {
        pending.get("5")![0].resolve("Stale page from the closed archive");
        finishActivity([{ eventId: "stale", conversationId: "first-archive", timestamp: 1, kind: "message", role: "user", source: "OpenCore", title: "Old activity", content: "Stale activity from the closed archive", metadata: {}, contentBytes: 38, truncated: false }]);
      });
      expect(screen.queryByText("Stale page from the closed archive")).not.toBeInTheDocument();
      expect(screen.queryByText("Stale activity from the closed archive")).not.toBeInTheDocument();
      expect(within(reopenedFifth).getByRole("button", { name: "Opening…" })).toBeDisabled();
      await act(async () => { pending.get("5")![1].resolve("Fresh verified fifth page"); });
      expect(within(reopenedFifth).getByText("Fresh verified fifth page")).toBeVisible();
      expect(within(reopenedFifth).getByRole("button", { name: "Reload" })).toBeEnabled();
    } finally {
      await act(async () => { for (const requests of pending.values()) for (const request of requests) request.resolve("Remaining exact page"); finishActivity([]); });
      overview.mockRestore(); events.mockRestore(); pages.mockRestore(); read.mockRestore();
    }
  });

  it("requires an explicit confirmation before enabling an allow everything mode", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: /Approval/ }));
    fireEvent.click(screen.getByRole("button", { name: "Allow everything in this chat" }));
    expect(screen.getByRole("dialog", { name: "Allow everything in this chat?" })).toBeInTheDocument();
    expect(document.querySelector(".chat-composer .approval-trigger")).toHaveAttribute("aria-label", "Approval: Ask every time");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(document.querySelector(".chat-composer .approval-trigger")).toHaveAttribute("aria-label", "Approval: Allow everything in this chat");
  });

  it("sends the chosen approval mode with the message", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: /Approval/ }));
      fireEvent.click(screen.getByRole("button", { name: "Approve for me" }));
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Find the helper" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Find the helper", [], expect.any(String), "approve-for-me", [], true, 3, true, 200000, expect.any(String)));
    } finally { send.mockRestore(); }
  });

  it("selects a slash skill and sends it with the task", async () => {
    const library = await api.modelLibrary();
    const models=vi.spyOn(api,'installedSkillModels').mockResolvedValue(library.models.filter(model=>model.category==='computer-use').map(model=>({id:model.id,category:'computer-use',installed:true})));
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "/computer-use open Calculator" } });
      expect(await screen.findByRole("listbox", { name: "Skills" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("option", { name: /computer-use/ }));
      expect(screen.getByLabelText("Message OpenCore")).toHaveValue("open Calculator");
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "open Calculator", [], expect.any(String), "ask-every-time", ["computer-use"], true, 3, true, 200000, expect.any(String)));
    } finally { send.mockRestore(); models.mockRestore(); }
  });

  it("sends tool choices configured in Settings and the exact context threshold", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(screen.getByRole("button", { name: "Project skills enabled" }));
      fireEvent.click(screen.getByRole("button", { name: "Chrome" }));
      expect(screen.getByRole("button", { name: "Chrome" })).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByLabelText('Auto-compaction trigger (tokens)')).toHaveValue(200000);
      expect(screen.queryByLabelText("Maximum answer length")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
      expect(screen.queryByRole("button", { name: /Prompt tools/ })).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Check my local dev page" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Check my local dev page", [], expect.any(String), "ask-every-time", ["chrome-control"], true, 3, false, 200000, expect.any(String)));
    } finally { send.mockRestore(); }
  });

  it("previews and downloads a generated HTML artifact", async () => {
    const id = "802b4e3a-0b84-4906-9964-324534dacbd9";
    const original = api.conversation;
    const history = vi.spyOn(api, "conversation").mockImplementation(async (conversationId) => [
      ...await original(conversationId),
      { id: 3001, conversationId, timestamp: new Date().toISOString(), kind: "file", role: "assistant", source: "OpenCore", title: "game.html", content: `artifact://${id}`, metadata: { id, name: "game.html", mime: "text/html", size: 29 } },
    ]);
    const preview = vi.spyOn(api, "previewArtifact").mockResolvedValue({ id, name: "game.html", mime: "text/html", size: 29, dataUrl: "data:text/html;base64,PGgxPlBsYXk8L2gxPg==", text: "<h1>Play</h1>" });
    const download = vi.spyOn(api, "downloadArtifact").mockResolvedValue("C:\\Downloads\\game.html");
    try {
      render(<App />);
      expect(await screen.findByText("game.html")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Preview" }));
      const browser = await screen.findByRole("complementary", { name: "Workspace" });
      await waitFor(() => expect(browser.querySelector("iframe")).toHaveAttribute("srcdoc", "<h1>Play</h1>"));
      fireEvent.click(browser.querySelector(".workspace-file-toolbar button")!);
      await waitFor(() => expect(download).toHaveBeenCalledWith(id));
    } finally { history.mockRestore(); preview.mockRestore(); download.mockRestore(); }
  });

  it("renders an image artifact inline and opens its preview", async () => {
    const id = "802b4e3a-0b84-4906-9964-324534dacbd9";
    const original = api.conversation;
    const history = vi.spyOn(api, "conversation").mockImplementation(async (conversationId) => [
      ...await original(conversationId),
      { id: 3002, conversationId, timestamp: new Date().toISOString(), kind: "message", role: "assistant", source: "OpenCore", title: "Image", content: `![Generated chart](artifact://${id})`, metadata: {} },
    ]);
    const preview = vi.spyOn(api, "previewArtifact").mockResolvedValue({ id, name: "chart.svg", mime: "image/svg+xml", size: 12, dataUrl: "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", text: "<svg></svg>" });
    try {
      render(<App />);
      const image = await screen.findByRole("img", { name: "Generated chart" });
      expect(image).toHaveAttribute("src", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=");
      fireEvent.click(screen.getByRole("button", { name: "Preview Generated chart" }));
      expect(await screen.findByRole("complementary", { name: "Workspace" })).toBeInTheDocument();
    } finally { history.mockRestore(); preview.mockRestore(); }
  });

  it("keeps a sent image visible and renders a generated image artifact", async () => {
    const sentId = "53fa28cd-e578-4b73-855b-8cbe46709fc1";
    const generatedId = "a3e78852-0281-4c05-8ed9-fdb2cba9247a";
    const imageUrl = "data:image/png;base64,iVBORw0KGgo=";
    const original = api.conversation;
    const history = vi.spyOn(api, "conversation").mockImplementation(async (conversationId) => [
      ...await original(conversationId),
      { id: 3003, conversationId, timestamp: new Date().toISOString(), kind: "message", role: "user", source: "OpenCore", title: "You", content: "Look at this", metadata: { files: [{ name: "sample.png", path: "C:\\Temp\\sample.png", artifactId: sentId }] } },
      { id: 3004, conversationId, timestamp: new Date().toISOString(), kind: "file", role: "assistant", source: "OpenCore", title: "result.png", content: `artifact://${generatedId}`, metadata: { id: generatedId, name: "result.png", mime: "image/png", size: 12 } },
    ]);
    const preview = vi.spyOn(api, "previewArtifact").mockImplementation(async (id) => ({ id, name: id === sentId ? "sample.png" : "result.png", mime: "image/png", size: 12, dataUrl: imageUrl, text: null }));
    try {
      render(<App />);
      expect(await screen.findByRole("img", { name: "sample.png" })).toHaveAttribute("src", imageUrl);
      expect(await screen.findByRole("img", { name: "result.png" })).toHaveAttribute("src", imageUrl);
      expect(preview).toHaveBeenCalledWith(sentId);
      expect(preview).toHaveBeenCalledWith(generatedId);
    } finally { history.mockRestore(); preview.mockRestore(); }
  });

  it("shows a selected image before sending and removes its preview with the attachment", async () => {
    const path = "C:\\Temp\\draft.png";
    const dataUrl = "data:image/png;base64,iVBORw0KGgo=";
    const picker = vi.mocked(dialog.open).mockResolvedValue(path);
    const preview = vi.spyOn(api, "previewAttachmentImage").mockResolvedValue(dataUrl);
    const openPreview = vi.spyOn(api, "previewComposerAttachment").mockResolvedValue({ name: "draft.png", mime: "image/png", size: 12, dataUrl, text: null });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Upload files or images" }));
      expect(await screen.findByRole("img", { name: "draft.png" })).toHaveAttribute("src", dataUrl);
      expect(preview).toHaveBeenCalledWith(path);
      fireEvent.click(screen.getByRole("button", { name: "Preview draft.png" }));
      const browser = await screen.findByRole("complementary", { name: "Workspace" });
      await waitFor(() => expect(browser.querySelector(".workspace-file-view img")).toHaveAttribute("src", dataUrl));
      expect(openPreview).toHaveBeenCalledWith(path);
      fireEvent.click(screen.getByRole("button", { name: "Close workspace" }));
      fireEvent.click(screen.getByRole("button", { name: "Remove draft.png" }));
      expect(screen.queryByRole("img", { name: "draft.png" })).not.toBeInTheDocument();
    } finally { picker.mockReset(); preview.mockRestore(); openPreview.mockRestore(); }
  });

  it("shows project tool activity while the model is still working", async () => {
    const originalConversation = api.conversation;
    let working = false;
    const send = vi.spyOn(api, "sendChatMessage").mockImplementation(() => {
      working = true;
      return new Promise(() => {});
    });
    const history = vi.spyOn(api, "conversation").mockImplementation(async (id) => {
      const rows = await originalConversation(id);
      return working ? [...rows, {
        id: 9999, conversationId: id, timestamp: new Date().toISOString(), kind: "tool_call",
        role: "assistant", source: "OpenCore", title: "read_project_file",
        content: '{"path":"src/main.py"}', metadata: {},
      }] : rows;
    });
    try {
      const view = render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Read the project" } });
      fireEvent.click(screen.getByTitle("Send"));
      expect(await screen.findByText("Read files", { selector: "summary span" }, { timeout: 4000 })).toBeInTheDocument();
      view.unmount();
    } finally { send.mockRestore(); history.mockRestore(); }
  });

  it("keeps older tool evidence without inventing assistant narration", async () => {
    const original = api.snapshot;
    const snapshot = vi.spyOn(api, "snapshot").mockImplementation(async () => ({ ...await original(), activeConversationIds: ["preview"] }));
    const row = (id: number, kind: string, role: string, title: string, content: string) => ({
      id, conversationId: "preview", timestamp: new Date().toISOString(), kind, role, source: "OpenCore", title, content, metadata: {},
    });
    const history = vi.spyOn(api, "conversation").mockResolvedValue([
      row(1, "message", "user", "You", "Check my desktop"),
      row(2, "thinking", "assistant", "Reasoning", "Find open windows"),
      row(3, "tool_call", "assistant", "desktop_use", '{"function":{"name":"desktop_use","arguments":"{\\"action\\":\\"list\\"}"}}'),
      row(4, "tool_result", "tool", "desktop_use", "{}"),
      row(5, "thinking", "assistant", "Reasoning", "Use the browser"),
      row(6, "tool_call", "assistant", "browser_use", '{"function":{"name":"browser_use","arguments":"{\\"action\\":\\"inspect\\"}"}}'),
      row(7, "tool_result", "tool", "browser_use", "{}"),
    ]);
    try {
      render(<App />);
      await screen.findAllByText("Used 1 tool");
      expect(screen.queryByText("I'll check which Windows apps are open.")).not.toBeInTheDocument();
      expect(Array.from(document.querySelector(".assistant-response")!.children).map((node) =>
        node.classList.contains("kind-thinking") ? "reasoning" : node.classList.contains("tool-group") ? "tools"
          : node.classList.contains("assistant-progress") ? "narration" : "other"
      )).toEqual(["reasoning", "tools", "reasoning", "tools"]);
      const reasoning = document.querySelectorAll<HTMLDetailsElement>(".kind-thinking");
      expect(reasoning).toHaveLength(2);
      expect(reasoning[0]).not.toHaveAttribute("open");
      expect(reasoning[0].textContent).toContain("Find open windows");
      expect(reasoning[1].textContent).toContain("Use the browser");
      expect(screen.getAllByText("Reasoned")).toHaveLength(2);
      const groups = document.querySelectorAll<HTMLDetailsElement>(".tool-group");
      expect(groups).toHaveLength(2);
      for (const group of groups) {
        expect(group).toHaveTextContent("Used 1 tool");
        expect(group.querySelectorAll(".tool-chain li")).toHaveLength(1);
      }
    } finally { snapshot.mockRestore(); history.mockRestore(); }
  });

  it("shows imported ECHO indexing as an in-place progress card", async () => {
    const original = api.conversation;
    const history = vi.spyOn(api, "conversation").mockImplementation(async (id) => [
      ...await original(id),
      { id: 2222, conversationId: id, timestamp: new Date().toISOString(), kind: "echo_import",
        role: "system", source: "OpenCore", title: "Preparing ECHO",
        content: "Indexed 3/10 imported turns", metadata: { status: "indexing", current: 3, total: 10 } },
    ]);
    try {
      render(<App />);
      expect(await screen.findByText("Indexed 3/10 imported turns")).toBeInTheDocument();
      expect(screen.getByRole("progressbar", { name: "ECHO import progress" })).toHaveAttribute("value", "3");
    } finally { history.mockRestore(); }
  });

  it("uses the focused, fast conversation surface", async () => {
    render(<App />);
    expect(await screen.findByText("Build a data analysis script", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recent" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "OpenCore" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "Claude Code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Codex" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message OpenCore")).toBeInTheDocument();
    expect(screen.queryByText("Telemetry")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Runtime & Logs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(await screen.findByText("Ran a command", { selector: "summary span" })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".aui-md pre code")).toBeInTheDocument());
    expect(screen.queryByText("You", { selector: ".aui-message-meta span" })).not.toBeInTheDocument();
  });

  it("opens the full OpenCore workspace only when requested", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Runtime & Logs" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Runtime & Logs", level: 1 })).toBeInTheDocument());
    expect(screen.getByText("Runtime Topology")).toBeInTheDocument();
    expect(screen.getByText("Control Gateway")).toBeInTheDocument();
    expect(screen.getByText("Process Supervision")).toBeInTheDocument();
  });

  it("shows only downloaded models in the Runtime profile switcher", async () => {
    const inventory = await api.modelLibrary();
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({
      ...inventory,
      models: inventory.models.map(model => ({ ...model, installed: ["echo", "doucode"].includes(model.id) })),
    });
    try {
      render(<App />);
      await screen.findByText("Conversations", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Runtime & Logs" }));
      const profileSwitch = document.querySelector(".profile-switch") as HTMLElement;
      expect(await within(profileSwitch).findByRole("button", { name: /ECHO 3T/ })).toBeVisible();
      expect(within(profileSwitch).getByRole("button", { name: /DuoCore/ })).toBeVisible();
      expect(within(profileSwitch).queryByRole("button", { name: /Swift 1.5/ })).not.toBeInTheDocument();
    } finally { library.mockRestore(); }
  });

  it("exposes Claude Code and Codex connectors without extra conversation tabs", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
    expect(await screen.findByText("Claude Code", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Codex", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add profile" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Sync history" }).length).toBeGreaterThanOrEqual(2);
  });

  it("opens the styled model picker with DuoCore selectable and keeps Unsloth out of runtime profiles", async () => {
    const inventory = await api.modelLibrary();
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ ...inventory, models: inventory.models.map(model => ({ ...model, installed: ["echo", "native1m", "doucode"].includes(model.id) })) });
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Overview" }));
    const topbar = within(document.querySelector(".statusbar") as HTMLElement);
    const picker = topbar.getByRole("button", { name: /Choose model profile, currently DuoCore · ECHO/ });
    fireEvent.click(picker);
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    expect(await screen.findByRole("button", { name: /ECHO 3T Addressable history target/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /DuoCore · ECHO K2 \+ Nanbeige · competing drafts, one selected answer · ECHO archive/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /ECHO 3T Addressable history target/ }));
    expect(await topbar.findByRole("button", { name: /Choose model profile, currently ECHO 3T/ })).toBeVisible();
    fireEvent.click(topbar.getByRole("button", { name: /Choose model profile, currently ECHO 3T/ }));
    fireEvent.click(await screen.findByRole("button", { name: /1M extended · ECHO.*1,000,000-token YaRN window.*ECHO archive.*trained context 262,144/ }));
    expect(await topbar.findByRole("button", { name: /Choose model profile, currently 1M extended · ECHO/ })).toBeVisible();
    fireEvent.click(topbar.getByRole("button", { name: /Choose model profile, currently 1M extended · ECHO/ }));
    expect(await screen.findByRole("button", { name: /ECHO 3T Addressable history target/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
    expect(await screen.findByText("Unsloth", { selector: "h2" })).toBeInTheDocument();
    library.mockRestore();
  });

  it("opens upload and model choices from the composer and switches models from the footer", async () => {
    const inventory = await api.modelLibrary();
    const library = vi.spyOn(api, "modelLibrary").mockResolvedValue({ ...inventory, models: inventory.models.map(model => ({ ...model, installed: ["echo", "native1m", "doucode"].includes(model.id) })) });
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });

    fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
    expect(screen.getByRole("menu", { name: "Composer actions" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Upload files or images" })).toBeVisible();
    fireEvent.click(screen.getByRole("menuitem", { name: /Model DuoCore/ }));
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: /1M extended · ECHO.*1,000,000-token YaRN window.*ECHO archive.*trained context 262,144/ }));

    const footer = document.querySelector(".statusbar");
    expect(footer).toContainElement(screen.getByRole("button", { name: "Choose model profile, currently 1M extended · ECHO" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose model profile, currently 1M extended · ECHO" }));
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: /ECHO 3T Addressable history target/ }));
    expect(footer).toContainElement(await screen.findByRole("button", { name: "Choose model profile, currently ECHO 3T" }));
    library.mockRestore();
  });

  it("shows ECHO's 3T archive goal for the extended profile in the footer", async () => {
    const workingSet = vi.spyOn(api, "echoWorkingSet").mockResolvedValue({
      available: true,
      liveTokens: 131072,
      promptTokens: 131072,
      modelActiveTokens: 131072,
      modelContextTokens: 262144,
      windowTokens: 262144,
      active: true,
      offloadedMessages: 7,
      contextMode: "native",
    });
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>([["opencore.model-profile", "native1m"]]);
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    try {
      const { unmount } = render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      const extendedStatusbar = document.querySelector(".statusbar") as HTMLElement;
      expect(await within(extendedStatusbar).findByText(/3T archive goal/)).toBeVisible();
      await waitFor(() => expect(within(extendedStatusbar).getByRole("progressbar", { name: "ECHO model context usage" })).toHaveAttribute("value", "131072"));

      unmount();
      values.set("opencore.model-profile", "echo");
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      const statusbar = document.querySelector(".statusbar") as HTMLElement;
      expect(await within(statusbar).findByText(/3T archive goal/)).toBeVisible();
      expect(await within(statusbar).findByLabelText("ECHO archived messages")).toHaveTextContent("7 archived");
      expect(within(statusbar).getByRole("progressbar", { name: "ECHO model context usage" })).toHaveAttribute("value", "131072");
      expect(within(statusbar).getByLabelText("ECHO context and archive")).toHaveAttribute(
        "title",
        expect.stringContaining("not simultaneous model attention"),
      );
    } finally {
      workingSet.mockRestore();
      if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
    }
  });

  it("shows ECHO's addressable history target in the bottom status bar for DuoCore", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    const target = await screen.findByLabelText("ECHO context and archive");
    const statusbar = target.closest("footer");
    expect(statusbar).toHaveClass("statusbar");
    expect(target).toHaveTextContent("3T archive goal");
    expect(statusbar).toContainElement(screen.getByRole("button", { name: "Choose model profile, currently DuoCore · ECHO" }));
    expect(statusbar?.textContent).toContain("VRAM");
    expect(statusbar?.textContent).toContain("tokens/s");
    expect(document.querySelector(".chat-composer .echo-context-status")).toBeNull();
  });

  it("shows Nanbeige BF16 ECHO as an ECHO archive profile in the footer", async () => {
    vi.spyOn(api, "echoWorkingSet").mockResolvedValue({
      available: true, liveTokens: 9000, promptTokens: 1200, modelActiveTokens: 1200,
      modelContextTokens: 262144, windowTokens: 262144, active: false,
      offloadedMessages: 4, contextMode: "persistent_echo",
    });
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>([["opencore.model-profile", "nanbeige-bf16-echo"]]);
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    try {
      render(<App />);
      await screen.findByText("Conversations", { selector: "h2" });
      const footer = document.querySelector(".statusbar") as HTMLElement;
      expect(await within(footer).findByLabelText("ECHO context and archive")).toBeVisible();
      expect(await within(footer).findByText(/4 archived/)).toBeVisible();
      expect(within(footer).getByRole("button", { name: /Nanbeige BF16 ECHO/ })).toBeVisible();
    } finally {
      vi.restoreAllMocks();
      if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
    }
  });

  it("shows auto-compaction telemetry for a native model in the footer", async () => {
    vi.spyOn(api, "echoWorkingSet").mockResolvedValue({
      available: true, liveTokens: 190000, promptTokens: 190000, modelActiveTokens: 190000,
      modelContextTokens: 262144, windowTokens: 262144, active: false,
      autoCompactEnabled: true, autoCompactThreshold: 200000, compactions: 2,
      contextMode: "native-kv",
    });
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>([["opencore.model-profile", "nanbeige-bf16"]]);
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    try {
      render(<App />);
      await screen.findByText("Conversations", { selector: "h2" });
      const footer = document.querySelector(".statusbar") as HTMLElement;
      expect(await within(footer).findByText(/Auto compact · 200,000/)).toBeVisible();
      expect(within(footer).getByText(/2 compactions/)).toBeVisible();
      expect(within(footer).getByRole("button", { name: /Nanbeige BF16/ })).toBeVisible();
    } finally {
      vi.restoreAllMocks();
      if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
    }
  });

  it("retains the last measured ECHO window usage when idle", async () => {
    const workingSet = vi.spyOn(api, "echoWorkingSet").mockResolvedValue({
      available: true,
      liveTokens: 131072,
      promptTokens: 8192,
      modelContextTokens: 262144,
      windowTokens: 32768,
      active: false,
      offloadedMessages: 7,
      contextMode: "persistent_echo",
    });
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>([["opencore.model-profile", "echo"]]);
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      const statusbar = document.querySelector(".statusbar") as HTMLElement;
      expect(await within(statusbar).findByText(/3T archive goal/)).toBeVisible();
      // The static archive label renders before the asynchronous usage snapshot arrives.
      expect(await within(statusbar).findByText(/8.2K \/ 32.8K last/)).toBeVisible();
      expect(within(statusbar).getByRole("progressbar", { name: "ECHO model context usage" })).toHaveAttribute("value", "8192");
      expect(within(statusbar).getByLabelText("ECHO archived messages")).toHaveTextContent("7 archived");
      expect(within(statusbar).getByLabelText("ECHO context and archive")).toHaveAttribute(
        "title",
        expect.stringContaining("not simultaneous model attention"),
      );
    } finally {
      workingSet.mockRestore();
      if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
    }
  });

  it("uses an OpenCore rename modal instead of a browser hostname prompt", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByTitle("Rename"));
    expect(screen.getByRole("dialog", { name: "Rename conversation" })).toBeInTheDocument();
    expect(screen.getByLabelText("Conversation name")).toBeInTheDocument();
    expect(screen.getByText("OpenCore", { selector: ".modal-brand span" })).toBeInTheDocument();
  });

  it("keeps every app system reachable and supports collapsible source and project groups", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    for (const name of ["Overview", "Conversations", "Memory", "Runtime & Logs", "Models", "Connectors", "Settings", "Troubleshooting"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    const recent = screen.getByRole("button", { name: "Recent group, 4" });
    expect(recent).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(recent);
    expect(recent).toHaveAttribute("aria-expanded", "false");
    const projects = screen.getByRole("button", { name: "Projects group, 3" });
    expect(projects).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(projects);
    expect(projects).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "AI Research group, 1" })).toBeInTheDocument();
  });

  it("uses a styled project menu with a real create flow instead of a native select", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    expect(screen.queryByRole("combobox", { name: "Conversation project" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Project: OpenCore" }));
    expect(screen.getByRole("menu", { name: "Move conversation to project" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: "No project" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Create project" }));
    expect(screen.getByRole("textbox", { name: "New project name" })).toBeInTheDocument();
  });

  it("requires a chosen folder before creating a project", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "+ Project" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Project name" }), { target: { value: "Research" } });
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose folder" })).toBeInTheDocument();
  });

  it("keeps an imported Codex project bound to its source folder", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Projects group, 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Options for Work" }));
    expect(screen.getByRole("menuitem", { name: "Change folder" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Remove from OpenCore" })).toBeDisabled();
  });

  it("applies the shared appearance source and autosaves local terminal preferences", async () => {
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    const source = platform.defaultPlatformConfiguration();
    let currentConfiguration = source;
    const state = { configuration: source, loading: false, error: '', preview: true, reload: vi.fn(async () => {}), save: vi.fn(async () => source), savePatch: vi.fn(async () => source), getCurrentConfiguration: () => currentConfiguration };
    const sourceHook = vi.spyOn(platform, 'usePlatformConfiguration').mockReturnValue(state);
    const view = render(<App />);
    try {
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.queryByLabelText(/Message text size/)).not.toBeInTheDocument();
    expect(screen.getAllByLabelText('Text size (pixels)')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(/Terminal\/log text/), { target: { value: '16' } });
    fireEvent.click(screen.getByRole("button", { name: "Project skills enabled" }));
    currentConfiguration = { ...source, compactAtTokens: 250000, appearance: { ...source.appearance, fontSize: 17, density: 'compact' } };
    sourceHook.mockReturnValue({ ...state, configuration: currentConfiguration });
    view.rerender(<App />);
    await waitFor(() => expect(screen.getByText(/Effective trigger for the configured .* model window:/)).toHaveTextContent('209,716 tokens'));
    fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
    await waitFor(() => expect(document.querySelector(".app-window-frame")).toHaveClass("compact-messages"));
    expect((document.querySelector(".app-window-frame") as HTMLElement).style.getPropertyValue("--chat-font-size")).toBe("17px");
    expect(JSON.parse(window.localStorage.getItem("opencore.appearance.v2") || "{}")).toMatchObject({ chatFontSize: 17, terminalFontSize: 16, compactMessages: true, projectSkillsEnabled: false, compactAtTokens: 250000 });
    } finally { view.unmount(); sourceHook.mockRestore(); if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage); }
  });

  it("shows persisted history-sync file progress in its connector status", async () => {
    const operations = vi.spyOn(api, "listOperations").mockResolvedValue([{
      id: "sync-1", kind: "history_sync", target: "codex", phase: "Importing transcripts", status: "running",
      current: 3, total: 10, imported: 1, updated: 1, skipped: 1, summary: "", error: null,
      startedAt: "2026-09-22T12:00:00Z", lastProgressAt: new Date().toISOString(), finishedAt: null,
    }]);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      expect(await screen.findByRole("button", { name: "Importing transcripts · 3/10 files" })).toBeDisabled();
      expect(screen.getByText("Importing transcripts · 3/10 files · updating")).toBeInTheDocument();
    } finally { operations.mockRestore(); }
  });

  it("keeps native connector failures and cancelled clearing distinct from success", async () => {
    const initial = await api.snapshot();
    const snapshot = vi.spyOn(api, "snapshot").mockResolvedValue({ ...initial, connectors: [{
      id: "opencode", name: "OpenCode", kind: "history", status: "not configured", endpoint: "",
      observable: true, details: "Connect the local model and import projects", custom: false,
    }] });
    const configure = vi.spyOn(api, "configureAgentConnector").mockRejectedValue(new Error("OpenCode config is not writable"));
    const sync = vi.spyOn(api, "startHistorySync").mockRejectedValue(new Error("Close OpenCode before importing"));
    const clear = vi.spyOn(api, "clearImportedHistory").mockResolvedValue("History cleared");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      const card = screen.getByRole("heading", { name: "OpenCode" }).closest("article") as HTMLElement;
      fireEvent.click(within(card).getByRole("button", { name: "Connect OpenCore" }));
      expect(await within(card).findByRole("alert")).toHaveTextContent("OpenCode config is not writable");
      expect(screen.queryByText("OpenCode connected to OpenCore")).not.toBeInTheDocument();
      fireEvent.click(within(card).getByRole("button", { name: "Sync chats and projects" }));
      await waitFor(() => expect(within(card).getByRole("alert")).toHaveTextContent("Close OpenCode before importing"));
      expect(screen.queryByText("OpenCode history import started")).not.toBeInTheDocument();
      fireEvent.click(within(card).getByRole("button", { name: "Clear imported" }));
      await waitFor(() => expect(confirm).toHaveBeenCalled());
      expect(clear).not.toHaveBeenCalled();
      expect(screen.queryByText("OpenCode copied history cleared")).not.toBeInTheDocument();
    } finally { snapshot.mockRestore(); configure.mockRestore(); sync.mockRestore(); clear.mockRestore(); confirm.mockRestore(); }
  });

  it("reports observed connector activity without claiming its profile is missing", async () => {
    const initial = await api.snapshot();
    const snapshot = vi.spyOn(api, "snapshot").mockResolvedValue({ ...initial, connectors: [{
      id: "hermes", name: "Hermes Agent", kind: "history", status: "observed", endpoint: "",
      observable: true, details: "OpenCore profile is installed. Requests observed.", custom: false,
    }] });
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      const card = screen.getByRole("heading", { name: "Hermes Agent" }).closest("article") as HTMLElement;
      expect(within(card).getByText("Local requests observed")).toBeVisible();
      expect(within(card).queryByText("Profile not added")).not.toBeInTheDocument();
    } finally { snapshot.mockRestore(); }
  });

  it("handles a failed Settings history sync without an unhandled action promise", async () => {
    const sync = vi.spyOn(api, "startHistorySync").mockRejectedValue(new Error("Source history is unavailable"));
    try {
      render(<App />);
      await screen.findByLabelText("Message OpenCore");
      fireEvent.click(screen.getByRole("button", { name: "Settings" }));
      fireEvent.click(await screen.findByRole("button", { name: "Sync local histories" }));
      await waitFor(() => expect(sync).toHaveBeenCalledWith("codex"));
      expect(await screen.findByText("Error: Source history is unavailable")).toBeVisible();
    } finally { sync.mockRestore(); }
  });

  it("opens the actual Windows model directory and reports Explorer failures", async () => {
    const opener = vi.spyOn(api, "openLocalPath").mockRejectedValue(new Error("Explorer unavailable"));
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Models" }));
      fireEvent.click(screen.getByRole("button", { name: "Open model folder" }));
      await waitFor(() => expect(opener).toHaveBeenCalledWith("C:\\OpenCore"));
      expect(await screen.findByText(/Could not open folder: Error: Explorer unavailable/)).toBeInTheDocument();
    } finally { opener.mockRestore(); }
  });

  it("retains the verified import summary and timestamp on the connector card", async () => {
    const operations = vi.spyOn(api, "listOperations").mockResolvedValue([{
      id: "sync-2", kind: "history_sync", target: "claude-code", phase: "Completed", status: "completed",
      current: 10, total: 10, imported: 2, updated: 3, skipped: 5,
      summary: "Imported 2 · Updated 3 · Skipped 5", error: null,
      startedAt: "2026-09-22T12:00:00Z", finishedAt: "2026-09-22T12:00:10Z",
    }]);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      expect(await screen.findByRole("button", { name: "Imported 2 · Updated 3 · Skipped 5" })).toBeEnabled();
      expect(screen.getByText((text, element) => element?.tagName === "TIME" && text.includes("2026"))).toBeInTheDocument();
    } finally { operations.mockRestore(); }
  });

  it("shows a provider probe in place and keeps the button busy until the probe returns", async () => {
    let finish!: (message: string) => void;
    const probe = vi.spyOn(api, "testConnector").mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      const card = screen.getByRole("heading", { name: "LM Studio" }).closest("article");
      fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Test" }));
      expect(within(card as HTMLElement).getByRole("button", { name: "Testing…" })).toBeDisabled();
      expect(probe).toHaveBeenCalledWith("lmstudio", "http://127.0.0.1:1234");
      finish("Connected · 2 models available");
      expect(await within(card as HTMLElement).findByRole("status")).toHaveTextContent("Connected · 2 models available");
    } finally { probe.mockRestore(); }
  });

  it("can cancel a running transcript import and clear imported source history", async () => {
    const running = {
      id: "sync-codex", kind: "history_sync", target: "codex", phase: "Importing transcripts", status: "running",
      current: 3, total: 10, imported: 1, updated: 1, skipped: 1, summary: "", error: null,
      startedAt: "2026-09-22T12:00:00Z", finishedAt: null,
    } as const;
    const cancelled = { ...running, phase: "Cancelled", status: "cancelled" } as const;
    let reportedOperation: OperationRecord = running;
    const operations = vi.spyOn(api, "listOperations").mockImplementation(async () => [reportedOperation]);
    const cancel = vi.spyOn(api, "cancelHistorySync").mockImplementation(async () => { reportedOperation = cancelled; });
    const clear = vi.spyOn(api, "clearImportedHistory").mockResolvedValue("Cleared 2 imported codex conversations from OpenCore and ECHO");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      const codex = screen.getByRole("heading", { name: "Codex" }).closest("article") as HTMLElement;
      await within(codex).findByRole("button", { name: "Importing transcripts · 3/10 files" });
      fireEvent.click(within(codex).getByRole("button", { name: "Cancel Codex import" }));
      await waitFor(() => expect(cancel).toHaveBeenCalledWith("sync-codex"));
      expect(within(codex).getByText("Cancellation requested…")).toBeInTheDocument();
      const clearButton = within(codex).getByRole("button", { name: "Clear imported history" });
      await waitFor(() => expect(clearButton).toBeEnabled(), { timeout: 3000 });
      fireEvent.click(clearButton);
      await waitFor(() => expect(clear).toHaveBeenCalledWith("codex"));
      expect(confirm).toHaveBeenCalled();
      expect(await within(codex).findByText("Cleared 2 imported codex conversations from OpenCore and ECHO")).toBeInTheDocument();
    } finally {
      operations.mockRestore(); cancel.mockRestore(); clear.mockRestore(); confirm.mockRestore();
    }
  });
});
