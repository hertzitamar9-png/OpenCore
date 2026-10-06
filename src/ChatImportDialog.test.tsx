import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { open } from "@tauri-apps/plugin-dialog";
import { ChatImportDialog } from "./ChatImportDialog";
import type { ImportPreview, ImportReport } from "./chat-import-types";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));

const path = "C:\\exports\\hermes.jsonl";
const preview: ImportPreview = { sourceFormat: "hermes", sourcePath: path, conversations: 2, entries: 6,
  warnings: [], samples: [{ sourceConversationId: "session-a", title: "Saved conversation", entries: 3, warnings: [] }] };
const report: ImportReport = { sourceFormat: "hermes", sourcePath: path, imported: 1, updated: 0, skipped: 1,
  failed: 0, cancelled: false, current: 2, total: 2, warnings: [], conversations: [
    { conversationId: "import:hermes:a", sourceConversationId: "session-a", title: "Saved conversation", status: "imported", entries: 3, warnings: [] },
    { conversationId: "import:hermes:b", sourceConversationId: "session-b", title: "Already copied", status: "skipped", entries: 3, warnings: [] },
  ] };

afterEach(() => vi.clearAllMocks());

it("offers each source format and cannot import without a selected file", () => {
  render(<ChatImportDialog onClose={vi.fn()} onImport={vi.fn()} />);
  expect(screen.getByRole("combobox", { name: "Source format" })).toHaveValue("auto");
  for (const name of ["Auto detect", "OpenCore", "Hermes Agent", "OpenCode", "Codex", "Claude Code", "Generic JSON"]) {
    expect(screen.getByRole("option", { name })).toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: "Import chats" })).toBeDisabled();
});

it("previews OpenCode's original folder status and imports with the native source format", async () => {
  const file = "C:\\exports\\opencode.json";
  vi.mocked(open).mockResolvedValue(file);
  const inspect = vi.fn().mockResolvedValue({ ...preview, sourceFormat: "opencode", samples: [
    { ...preview.samples[0], sourceFolder: "C:\\work\\original-project", folderStatus: "missing" },
  ] });
  const importFile = vi.fn().mockResolvedValue({ ...report, sourceFormat: "opencode", conversations: [
    { ...report.conversations[0], sourceFolder: "C:\\work\\original-project", folderStatus: "missing" },
  ] });
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} onPreview={inspect} />);
  fireEvent.change(screen.getByRole("combobox", { name: "Source format" }), { target: { value: "opencode" } });
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  expect(await screen.findByText(/Original folder is missing/)).toHaveTextContent("C:\\work\\original-project");
  expect(inspect).toHaveBeenCalledWith(file, "opencode");
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  await waitFor(() => expect(importFile).toHaveBeenCalledWith(file, "opencode"));
  expect(await screen.findByRole("region", { name: "Import results" })).toHaveTextContent("Original folder is missing");
});

it("uses the native file picker and previews the source before importing", async () => {
  vi.mocked(open).mockResolvedValue(path);
  const inspect = vi.fn().mockResolvedValue(preview), importFile = vi.fn().mockResolvedValue(report);
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} onPreview={inspect} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  expect(await screen.findByText("2 conversations · 6 entries")).toBeInTheDocument();
  expect(inspect).toHaveBeenCalledWith(path, "auto");
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ directory: false, multiple: false,
    filters: [expect.objectContaining({ extensions: ["json", "jsonl", "db", "sqlite", "sqlite3"] })] }));
  expect(importFile).not.toHaveBeenCalled();
  expect(screen.getByText("Saved conversation")).toBeInTheDocument();
});

it("passes the explicit source and shows the exact import results", async () => {
  vi.mocked(open).mockResolvedValue(path);
  const importFile = vi.fn().mockResolvedValue(report), imported = vi.fn();
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} onImported={imported} />);
  fireEvent.change(screen.getByRole("combobox", { name: "Source format" }), { target: { value: "hermes" } });
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  expect(await screen.findByText("1 imported · 0 updated · 1 skipped")).toBeInTheDocument();
  expect(importFile).toHaveBeenCalledWith(path, "hermes");
  expect(imported).toHaveBeenCalledWith(report);
  expect(screen.getAllByText("Already copied")).toHaveLength(2);
});

it("keeps a failed preview visible and blocks import until review succeeds", async () => {
  vi.mocked(open).mockResolvedValue(path);
  const inspect = vi.fn().mockRejectedValueOnce(new Error("Malformed message 2")).mockResolvedValue(preview);
  const importFile = vi.fn();
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} onPreview={inspect} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Malformed message 2");
  expect(screen.getByRole("button", { name: "Import chats" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview again" }));
  expect(await screen.findByText("2 conversations · 6 entries")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled();
  expect(importFile).not.toHaveBeenCalled();
});

it("retains the selected file if the next file picker is cancelled", async () => {
  vi.mocked(open).mockResolvedValueOnce(path).mockResolvedValueOnce(null);
  const importFile = vi.fn().mockResolvedValue(report);
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  expect(await screen.findByText(path)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(open).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  await waitFor(() => expect(importFile).toHaveBeenCalledWith(path, "auto"));
});

it("reports an import failure and allows retry without claiming success", async () => {
  vi.mocked(open).mockResolvedValue(path);
  const importFile = vi.fn().mockRejectedValueOnce(new Error("Source changed; choose it again")).mockResolvedValue(report);
  const imported = vi.fn();
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} onImported={imported} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source changed");
  expect(imported).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  expect(await screen.findByText("1 imported · 0 updated · 1 skipped")).toBeInTheDocument();
});

it("shows malformed conversations and warnings alongside successful copies", async () => {
  vi.mocked(open).mockResolvedValue(path);
  const partial: ImportReport = { ...report, skipped: 0, failed: 1, warnings: ["A duplicate event was ignored."], conversations: [
    report.conversations[0], { ...report.conversations[1], status: "failed", error: "Message 2 has no role" },
  ] };
  render(<ChatImportDialog onClose={vi.fn()} onImport={vi.fn().mockResolvedValue(partial)} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  expect(await screen.findByText("Message 2 has no role")).toBeInTheDocument();
  expect(screen.getByText("A duplicate event was ignored.")).toBeInTheDocument();
  expect(screen.getByText("Failed")).toBeInTheDocument();
});

it("does not let an obsolete preview overwrite a new format selection", async () => {
  vi.mocked(open).mockResolvedValue(path);
  let resolveFirst!: (result: ImportPreview) => void;
  const inspect = vi.fn().mockImplementationOnce(() => new Promise<ImportPreview>((resolve) => { resolveFirst = resolve; }))
    .mockResolvedValue({ ...preview, sourceFormat: "generic", entries: 4 });
  render(<ChatImportDialog onClose={vi.fn()} onImport={vi.fn()} onPreview={inspect} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(inspect).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByRole("combobox", { name: "Source format" }), { target: { value: "generic" } });
  expect(await screen.findByText("2 conversations · 4 entries")).toBeInTheDocument();
  await act(async () => resolveFirst(preview));
  expect(screen.queryByText("2 conversations · 6 entries")).not.toBeInTheDocument();
});

it("guards closing while import runs and exposes cancellation when supplied", async () => {
  vi.mocked(open).mockResolvedValue(path);
  let finish!: (result: ImportReport) => void;
  const importFile = vi.fn().mockImplementation(() => new Promise<ImportReport>((resolve) => { finish = resolve; }));
  const close = vi.fn(), cancel = vi.fn();
  render(<ChatImportDialog onClose={close} onImport={importFile} onCancelImport={cancel} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel import" }));
  expect(cancel).toHaveBeenCalledTimes(1);
  await act(async () => finish({ ...report, cancelled: true }));
  expect(screen.getByText("Import cancelled")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(close).toHaveBeenCalledTimes(1);
});

it("closes on Escape and restores focus to the opener", () => {
  const opener = document.createElement("button");
  document.body.append(opener); opener.focus();
  const close = vi.fn();
  const { unmount } = render(<ChatImportDialog onClose={close} onImport={vi.fn()} />);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(close).toHaveBeenCalledTimes(1);
  unmount();
  expect(opener).toHaveFocus();
  opener.remove();
});

it("shows operation progress and the actual per-file import limits", async () => {
  vi.mocked(open).mockResolvedValue(path);
  let finish!: (result: ImportReport) => void;
  const importFile = vi.fn().mockImplementation(() => new Promise<ImportReport>((resolve) => { finish = resolve; }));
  render(<ChatImportDialog onClose={vi.fn()} onImport={importFile} progress={{ current: 3, total: 8 }} />);
  expect(screen.getByText(/64 MiB.*50,000 entries.*1,000 chats/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Choose file" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Import chats" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Import chats" }));
  expect(screen.getByRole("status")).toHaveTextContent("Importing 3 of 8 conversations");
  await act(async () => finish(report));
});
