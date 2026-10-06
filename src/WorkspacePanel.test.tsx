import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { WorkspacePanel } from "./WorkspacePanel";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
const props = {
  open: true, tab: "browser" as const, width: 480, snapPx: 0,
  onTabChange: () => {}, onClose: () => {}, onWidthChange: () => {}, onSnapChange: () => {},
  onNotice: () => {}, onOpenConversation: () => {}, sideChat: <div>Side chat</div>,
};
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("keeps the Computer selection and draft through expansion, restoration, and workspace reopening", async () => {
  const browser = vi.spyOn(api, "nativeBrowserCommand").mockResolvedValue({ open: true, url: "https://example.com" });
  vi.spyOn(api, "desktopCommand").mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows: [{ windowId: 10, title: "Notes", bounds: { left: 0, top: 0, width: 960, height: 540 } }] } as never;
    if (action === "screenshot") return { windowId: args.windowId, bounds: { left: 0, top: 0, width: 960, height: 540 }, dataUrl: "data:image/png;base64,YQ==" } as never;
    return { editable: true, value: "Draft", inputMode: "accessibility" } as never;
  });
  const { rerender } = render(<WorkspacePanel {...props} />);
  await waitFor(() => expect(screen.getByLabelText("Browser address")).toHaveValue("https://example.com"));
  browser.mockClear();
  rerender(<WorkspacePanel {...props} tab="computer" />);
  const picker = await screen.findByLabelText("Window");
  await waitFor(() => expect(picker.querySelector('option[value="10"]')).not.toBeNull());
  fireEvent.change(picker, { target: { value: "10" } });
  const image = await screen.findByAltText("Selected Windows app");
  vi.spyOn(image, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 480, height: 270, right: 480, bottom: 270, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.click(image, { clientX: 120, clientY: 100 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Saved view draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Expand computer view" }));
  expect(screen.getByRole("button", { name: "Restore computer view" })).toBeVisible();
  expect(picker).toHaveValue("10");
  expect(input).toHaveValue("Saved view draft");
  await waitFor(() => expect(browser).toHaveBeenCalledWith("hide"));
  expect(browser.mock.calls.some(([action]) => action === "show")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Restore computer view" }));
  rerender(<WorkspacePanel {...props} tab="computer" open={false} />);
  rerender(<WorkspacePanel {...props} tab="computer" />);
  expect(screen.getByLabelText("Window")).toHaveValue("10");
  expect(screen.getByLabelText("Type in selected window")).toHaveValue("Saved view draft");
  expect(screen.getByAltText("Selected Windows app")).toBe(image);
});

it("pauses Computer polling while workspace content is obscured", async () => {
  const computer = vi.spyOn(api, "desktopCommand").mockResolvedValue({ windows: [] });
  render(<WorkspacePanel {...props} tab="computer" obscured />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByLabelText("Window")).toBeInTheDocument();
  expect(computer).not.toHaveBeenCalled();
});
