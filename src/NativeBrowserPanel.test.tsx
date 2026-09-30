import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
const { openFileDialog } = vi.hoisted(() => ({ openFileDialog: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: openFileDialog }));
import * as api from "./api";
import { NativeBrowserPanel } from "./NativeBrowserPanel";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); openFileDialog.mockReset(); });

it("keeps a page opened by the model when the browser panel mounts", async () => {
  const command = vi.spyOn(api, "nativeBrowserCommand").mockResolvedValue({
    open: true, url: "http://127.0.0.1:8989/task/45",
  });
  render(<NativeBrowserPanel onClose={() => {}} onNotice={() => {}} preview={null}
    onDownload={() => {}} width={800}
    onWidthChange={() => {}} side="right" onSideChange={() => {}} snapPx={0}
    onSnapChange={() => {}} />);
  await waitFor(() => expect(screen.getByLabelText("Browser address")).toHaveValue("http://127.0.0.1:8989/task/45"));
  expect(screen.queryByRole("button", { name: "Expand browser" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Split browser" })).not.toBeInTheDocument();
  expect(command).toHaveBeenCalledWith("status");
  expect(command.mock.calls.some(([action]) => action === "open" || action === "navigate")).toBe(false);
});

it("opens and closes web tabs without replacing the other tab", async () => {
  const command = vi.spyOn(api, "nativeBrowserCommand").mockImplementation(async (action, args = {}) => {
    if (action === "status") return { open: !args.tabId, url: args.tabId ? undefined : "https://example.com" } as never;
    return { open: action !== "close", url: typeof args.url === "string" ? args.url : "https://example.com" } as never;
  });
  render(<NativeBrowserPanel onClose={() => {}} onNotice={() => {}} preview={null}
    onDownload={() => {}} width={800} onWidthChange={() => {}} side="right" onSideChange={() => {}}
    snapPx={0} onSnapChange={() => {}} />);
  await screen.findByLabelText("Browser address");

  vi.stubGlobal("crypto", { randomUUID: () => "next" });
  fireEvent.click(screen.getByRole("button", { name: "New web tab" }));
  await screen.findByRole("tab", { name: "Web 2" });
  await waitFor(() => expect(command).toHaveBeenCalledWith("open", expect.objectContaining({ tabId: "next" })));
  fireEvent.click(screen.getByRole("button", { name: "Close Web 2" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("close", { tabId: "next" }));
  expect(screen.queryByRole("tab", { name: "Web 2" })).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Web 1" })).toBeInTheDocument();
  vi.unstubAllGlobals();
});

it("opens selected files in their own closable tabs", async () => {
  const command = vi.spyOn(api, "nativeBrowserCommand").mockResolvedValue({ open: true, url: "https://example.com" });
  const first = { name: "model.png", mime: "image/png", size: 1, dataUrl: "data:image/png;base64,AA==", text: null };
  const second = { name: "notes.txt", mime: "text/plain", size: 5, dataUrl: "data:text/plain;base64,SGVsbG8=", text: "Hello" };
  vi.spyOn(api, "previewComposerAttachment").mockResolvedValue(second);
  openFileDialog.mockResolvedValueOnce("C:\\temp\\notes.txt");
  render(<NativeBrowserPanel onClose={() => {}} onNotice={() => {}} preview={first}
    onDownload={() => {}} width={800} onWidthChange={() => {}} side="right" onSideChange={() => {}}
    snapPx={0} onSnapChange={() => {}} />);
  await screen.findByRole("tab", { name: "model.png" });
  fireEvent.click(screen.getByRole("button", { name: "Open file in new tab" }));
  await screen.findByRole("tab", { name: "notes.txt" });
  expect(screen.getByText("Hello")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close model.png" }));
  expect(screen.queryByRole("tab", { name: "model.png" })).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "notes.txt" })).toBeInTheDocument();
  expect(command).toHaveBeenCalledWith("status");
});
