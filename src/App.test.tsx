import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App, { recentPromptProgress } from "./App";
import * as api from "./api";
import * as dialog from "@tauri-apps/plugin-dialog";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
const eventHandlers = vi.hoisted(() => new Map<string, (event: { payload: unknown }) => void>());
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (name: string, callback: (event: { payload: unknown }) => void) => {
  eventHandlers.set(name, callback);
  return () => { eventHandlers.delete(name); };
}) }));

describe("OpenCore", () => {
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
    window.localStorage.removeItem?.("opencore.model-profile");
    window.localStorage.removeItem?.("opencore.approval-global.v1");
    window.sessionStorage.removeItem?.("opencore.approval-chat.preview");
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
      expect(screen.queryByRole("button", { name: "Maximize OpenCore" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Move conversations|Dock conversations/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("slider", { name: "Reasoning effort" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Effort/ }));
      const selector = screen.getByRole("slider", { name: "Reasoning effort" });
      expect(selector).toHaveAttribute("max", "6");
      fireEvent.change(selector, { target: { value: "4" } });
      expect(selector).toHaveAttribute("aria-valuetext", "Extra high");
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Explain this change" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Explain this change", [], "extra-high", "ask-every-time", [], true, 3, true, 200000));
    } finally { send.mockRestore(); }
  });

  it("keeps effort and approval controls in the send bar and opens one padded panel at a time", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    const composer = document.querySelector(".chat-composer");
    expect(composer).toContainElement(screen.getByRole("button", { name: /Approval/ }));
    expect(composer).toContainElement(screen.getByRole("button", { name: /Effort/ }));
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
      fireEvent.click(screen.getByRole("button", { name: "Overview" }));
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
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Find the helper", [], expect.any(String), "approve-for-me", [], true, 3, true, 200000));
    } finally { send.mockRestore(); }
  });

  it("selects a slash skill and sends it with the task", async () => {
    const send = vi.spyOn(api, "sendChatMessage").mockResolvedValue({ conversationId: "c1", title: "Test" });
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "/computer-use open Calculator" } });
      expect(screen.getByRole("listbox", { name: "Skills" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("option", { name: /computer-use/ }));
      expect(screen.getByLabelText("Message OpenCore")).toHaveValue("open Calculator");
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "open Calculator", [], expect.any(String), "ask-every-time", ["computer-use"], true, 3, true, 200000));
    } finally { send.mockRestore(); }
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
      fireEvent.change(screen.getByLabelText(/Auto compact after/), { target: { value: "200000" } });
      expect(screen.queryByLabelText("Maximum answer length")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
      expect(screen.queryByRole("button", { name: /Prompt tools/ })).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Message OpenCore"), { target: { value: "Check my local dev page" } });
      fireEvent.click(screen.getByTitle("Send"));
      await waitFor(() => expect(send).toHaveBeenCalledWith(expect.any(String), "Check my local dev page", [], expect.any(String), "ask-every-time", ["chrome-control"], true, 3, false, 200000));
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
      const browser = await screen.findByRole("region", { name: "OpenCore Browser" });
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
      expect(await screen.findByRole("region", { name: "OpenCore Browser" })).toBeInTheDocument();
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
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Upload files or images" }));
      expect(await screen.findByRole("img", { name: "draft.png" })).toHaveAttribute("src", dataUrl);
      expect(preview).toHaveBeenCalledWith(path);
      fireEvent.click(screen.getByRole("button", { name: "Remove draft.png" }));
      expect(screen.queryByRole("img", { name: "draft.png" })).not.toBeInTheDocument();
    } finally { picker.mockReset(); preview.mockRestore(); }
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
      expect(await screen.findByText("Read files", {}, { timeout: 4000 })).toBeInTheDocument();
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
      await screen.findByText("Used 2 tools");
      expect(screen.queryByText("I'll check which Windows apps are open.")).not.toBeInTheDocument();
      expect(Array.from(document.querySelector(".assistant-response")!.children).map((node) =>
        node.classList.contains("kind-thinking") ? "reasoning" : node.classList.contains("tool-group") ? "tools"
          : node.classList.contains("assistant-progress") ? "narration" : "other"
      )).toEqual(["reasoning", "tools"]);
      const reasoning = document.querySelectorAll<HTMLDetailsElement>(".kind-thinking");
      expect(reasoning).toHaveLength(1);
      expect(reasoning[0]).not.toHaveAttribute("open");
      expect(reasoning[0].textContent).toContain("Find open windows");
      expect(reasoning[0].textContent).toContain("Use the browser");
      expect(screen.getAllByText("Reasoned")).toHaveLength(1);
      const group = document.querySelector<HTMLDetailsElement>(".tool-group")!;
      expect(group).toHaveTextContent("Used 2 tools");
      expect(group.querySelectorAll(".tool-chain li")).toHaveLength(2);
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
    expect(await screen.findByText("Ran a command")).toBeInTheDocument();
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

  it("exposes Claude Code and Codex connectors without extra conversation tabs", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
    expect(await screen.findByText("Claude Code", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Codex", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add profile" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Sync history" }).length).toBeGreaterThanOrEqual(2);
  });

  it("opens the styled model picker with doUcode selectable and keeps Unsloth out of runtime profiles", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Overview" }));
    const topbar = within(document.querySelector(".topbar") as HTMLElement);
    const picker = topbar.getByRole("button", { name: /Choose model profile, currently doUcode/ });
    fireEvent.click(picker);
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    expect(screen.getByRole("button", { name: /ECHO 3T 262,144 native context/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /doUcode K2 \+ Nanbeige · shared 262,144 context/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /ECHO 3T 262,144 native context/ }));
    expect(topbar.getByRole("button", { name: /Choose model profile, currently ECHO 3T/ })).toBeVisible();
    fireEvent.click(topbar.getByRole("button", { name: /Choose model profile, currently ECHO 3T/ }));
    fireEvent.click(screen.getByRole("button", { name: /Native 1M 1,000,000 token server window/ }));
    expect(topbar.getByRole("button", { name: /Choose model profile, currently Native 1M/ })).toBeVisible();
    fireEvent.click(topbar.getByRole("button", { name: /Choose model profile, currently Native 1M/ }));
    expect(screen.getByRole("button", { name: /ECHO 3T 262,144 native context/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
    expect(await screen.findByText("Unsloth", { selector: "h2" })).toBeInTheDocument();
  });

  it("opens upload and model choices from the composer and switches models from the footer", async () => {
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });

    fireEvent.click(screen.getByRole("button", { name: "Add files or choose model" }));
    expect(screen.getByRole("menu", { name: "Composer actions" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Upload files or images" })).toBeVisible();
    fireEvent.click(screen.getByRole("menuitem", { name: /Model doUcode/ }));
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Native 1M 1,000,000 token server window/ }));

    const footer = document.querySelector(".conversation-statusbar");
    expect(footer).toContainElement(screen.getByRole("button", { name: "Choose model profile, currently Native 1M" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose model profile, currently Native 1M" }));
    expect(screen.getByRole("group", { name: "Choose model profile" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /ECHO 3T 262,144 native context/ }));
    expect(footer).toContainElement(screen.getByRole("button", { name: "Choose model profile, currently ECHO 3T" }));
  });

  it("shows the measured ECHO rolling window and archived message count in the footer", async () => {
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
      const meter = await screen.findByRole("progressbar", { name: "Native context usage" });
      expect(meter).toHaveAttribute("max", "262144");
      expect(meter).toHaveAttribute("value", "131072");

      unmount();
      values.set("opencore.model-profile", "echo");
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      const echoMeter = await screen.findByRole("progressbar", { name: "ECHO model context usage" });
      expect(echoMeter).toHaveAttribute("max", "262144");
      expect(echoMeter).toHaveAttribute("value", "131072");
      expect(screen.getByLabelText("ECHO archived messages")).toHaveTextContent("7 archived");
    } finally {
      workingSet.mockRestore();
      if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
    }
  });

  it("shows the selected model and bounded context meter in the bottom status bar", async () => {
    render(<App />);
    await screen.findByText("Conversations", { selector: "h2" });
    const target = await screen.findByRole("progressbar", { name: "Native context usage" });
    const statusbar = target.closest("footer");
    expect(statusbar).toHaveClass("statusbar");
    expect(target).toHaveAttribute("max", "262144");
    expect(statusbar).toContainElement(screen.getByRole("button", { name: "Choose model profile, currently doUcode" }));
    expect(statusbar?.textContent).toContain("262K");
    expect(statusbar?.textContent).toContain("VRAM");
    expect(statusbar?.textContent).toContain("tokens/s");
    expect(document.querySelector(".chat-composer .echo-context-status")).toBeNull();
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

  it("saves and applies real conversation appearance settings", async () => {
    const originalStorage = Object.getOwnPropertyDescriptor(window, "localStorage");
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    } });
    render(<App />);
    await screen.findByText("Build a data analysis script", { selector: "h2" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.change(screen.getByLabelText(/Message text size/), { target: { value: "17" } });
    fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    fireEvent.click(screen.getByRole("button", { name: "Project skills enabled" }));
    fireEvent.change(screen.getByLabelText(/Auto compact after/), { target: { value: "200000" } });
    fireEvent.click(screen.getByRole("button", { name: "Conversations" }));
    await waitFor(() => expect(document.querySelector(".conversation-focus-shell")).toHaveClass("compact-messages"));
    expect((document.querySelector(".conversation-focus-shell") as HTMLElement).style.getPropertyValue("--chat-font-size")).toBe("17px");
    expect(JSON.parse(window.localStorage.getItem("opencore.appearance.v2") || "{}")).toMatchObject({ chatFontSize: 17, compactMessages: true, projectSkillsEnabled: false, compactAtTokens: 200000 });
    if (originalStorage) Object.defineProperty(window, "localStorage", originalStorage);
  });

  it("shows persisted history-sync file progress on its connector button", async () => {
    const operations = vi.spyOn(api, "listOperations").mockResolvedValue([{
      id: "sync-1", kind: "history_sync", target: "codex", phase: "Importing transcripts", status: "running",
      current: 3, total: 10, imported: 1, updated: 1, skipped: 1, summary: "", error: null,
      startedAt: "2026-09-22T12:00:00Z", finishedAt: null,
    }]);
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
      expect(await screen.findByRole("button", { name: "Scanning files… 3/10" })).toBeDisabled();
      expect(screen.getByText("Importing transcripts · 3/10 files")).toBeInTheDocument();
    } finally { operations.mockRestore(); }
  });

  it("opens the actual Windows model directory and reports Explorer failures", async () => {
    const opener = vi.spyOn(api, "openLocalPath").mockRejectedValue(new Error("Explorer unavailable"));
    try {
      render(<App />);
      await screen.findByText("Build a data analysis script", { selector: "h2" });
      fireEvent.click(screen.getByRole("button", { name: "Models" }));
      fireEvent.click(screen.getByRole("button", { name: "Open model folder" }));
      await waitFor(() => expect(opener).toHaveBeenCalledWith("C:\\Users\\hertz\\OpenCore"));
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
});
