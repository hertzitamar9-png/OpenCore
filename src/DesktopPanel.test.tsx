import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { DesktopPanel } from "./DesktopPanel";

const windows: api.DesktopWindow[] = [
  { windowId: 0, title: "Entire desktop", bounds: { left: 0, top: 0, width: 1920, height: 1080 } },
  { windowId: 10, title: "Notes", bounds: { left: -960, top: 80, width: 960, height: 540 } },
  { windowId: 20, title: "Calculator", bounds: { left: 120, top: 160, width: 640, height: 480 } },
];
function screenshot(windowId: number): api.DesktopShot {
  return { windowId, bounds: windows.find(item => item.windowId === windowId)!.bounds, dataUrl: windowId === 20 ? "data:image/png;base64,Yg==" : "data:image/png;base64,YQ==" };
}
function desktop() {
  return vi.spyOn(api, "desktopCommand").mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "interact") return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
    return { updated: true, submitted: false, inputMode: "accessibility", message: "Text updated. Activate a supported submit button in the application." } as never;
  });
}
async function selectWindow(id = 10) {
  const picker = await screen.findByLabelText("Window");
  await waitFor(() => expect(picker.querySelector(`option[value="${id}"]`)).not.toBeNull());
  fireEvent.change(picker, { target: { value: String(id) } });
  return screen.findByAltText("Selected Windows app");
}
function imageBounds(image: HTMLElement) {
  vi.spyOn(image, "getBoundingClientRect").mockReturnValue({ left: 100, top: 40, width: 480, height: 270, right: 580, bottom: 310, x: 100, y: 40, toJSON: () => ({}) });
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("maps screenshot clicks to the selected window and requires background control for every text update", async () => {
  const command = desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(screen.getByLabelText("Type in selected window")).toHaveValue("Existing note"));
  expect(command).toHaveBeenCalledWith("interact", { windowId: 10, x: 480, y: 270, backgroundOnly: true, allowForegroundFallback: false });
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "New note" } });
  await waitFor(() => expect(command).toHaveBeenCalledWith("set_at", { windowId: 10, x: 480, y: 270, text: "New note", backgroundOnly: true, allowForegroundFallback: false }));
  expect(command.mock.calls.some(([action]) => action === "click" || action === "focus")).toBe(false);
});

it("keeps the entire desktop capture view only instead of falling back to a foreground click", async () => {
  const command = desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(0);
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  expect(await screen.findByText(/Entire desktop is view only/)).toBeVisible();
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
  expect(command.mock.calls.some(([action]) => ["interact", "click", "commit_text", "commit_enter"].includes(action))).toBe(false);
});

it("preserves the draft when native text application cannot submit the control", async () => {
  const command = desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await screen.findByText(/Text updated.*Activate a supported submit button/);
  expect(input).toHaveValue("Keep this draft");
  expect(input).toBeEnabled();
  expect(command).toHaveBeenCalledWith("commit_text", { windowId: 10, x: 480, y: 270, text: "Keep this draft", backgroundOnly: true, allowForegroundFallback: false });
  expect(command.mock.calls.some(([action]) => action === "commit_enter")).toBe(false);
});

it("reports unsupported background controls without retrying through a foreground command", async () => {
  const command = desktop();
  const notice = vi.fn();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    throw new Error("This control does not support background interaction. Select a supported app control.");
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={notice} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  expect(await screen.findByRole("alert")).toHaveTextContent("This control does not support background interaction");
  expect(notice).toHaveBeenCalledWith(expect.stringContaining("This control does not support background interaction"));
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
  expect(command.mock.calls.filter(([action]) => action !== "list" && action !== "screenshot").map(([action]) => action)).toEqual(["interact"]);
});

it("does not replace the selected window with an older screenshot that finishes late", async () => {
  const command = desktop();
  let resolveOld!: (value: api.DesktopShot) => void;
  const oldShot = new Promise<api.DesktopShot>(resolve => { resolveOld = resolve; });
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return (args.windowId === 10 ? oldShot : screenshot(Number(args.windowId))) as never;
    return {} as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const picker = await screen.findByLabelText("Window");
  await waitFor(() => expect(picker.querySelector('option[value="10"]')).not.toBeNull());
  fireEvent.change(picker, { target: { value: "10" } });
  await waitFor(() => expect(command).toHaveBeenCalledWith("screenshot", { windowId: 10 }));
  fireEvent.change(picker, { target: { value: "20" } });
  await waitFor(() => expect(screen.getByAltText("Selected Windows app")).toHaveAttribute("src", "data:image/png;base64,Yg=="));
  await act(async () => { resolveOld(screenshot(10)); await oldShot; });
  expect(picker).toHaveValue("20");
  expect(screen.getByAltText("Selected Windows app")).toHaveAttribute("src", "data:image/png;base64,Yg==");
});

it("discards an edit result from a window that is no longer selected", async () => {
  const command = desktop();
  let resolveEdit!: (value: object) => void;
  const edit = new Promise(resolve => { resolveEdit = resolve; });
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return edit as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await selectWindow(20);
  await act(async () => { resolveEdit({ editable: true, value: "Wrong window draft", inputMode: "accessibility" }); await edit; });
  expect(screen.getByLabelText("Window")).toHaveValue("20");
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
  expect(screen.getByLabelText("Type in selected window")).not.toHaveValue("Wrong window draft");
});

it("preserves the screenshot, selection, and draft while expanding and consumes the first Escape", async () => {
  desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Unsaved draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Expand computer view" }));
  expect(screen.getByRole("button", { name: "Restore computer view" })).toBeVisible();
  expect(screen.getByLabelText("Window")).toHaveValue("10");
  expect(screen.getByAltText("Selected Windows app")).toBe(image);
  expect(input).toHaveValue("Unsaved draft");
  const applicationEscape = vi.fn();
  document.addEventListener("keydown", applicationEscape);
  try {
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Expand computer view" })).toBeVisible();
    expect(screen.getByLabelText("Window")).toHaveValue("10");
    expect(input).toHaveValue("Unsaved draft");
    expect(applicationEscape).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(applicationEscape).toHaveBeenCalledTimes(1);
  } finally { document.removeEventListener("keydown", applicationEscape); }
});

it("stops captures and cancels delayed text updates when the computer panel becomes inactive", async () => {
  const command = desktop();
  const props = { embedded: true, onClose: () => {}, onNotice: () => {} };
  const { rerender } = render(<DesktopPanel {...props} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(screen.getByLabelText("Type in selected window")).toBeEnabled());
  vi.useFakeTimers();
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "Do not send after hiding" } });
  rerender(<DesktopPanel {...props} active={false} />);
  command.mockClear();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(command).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Type in selected window")).toHaveValue("Do not send after hiding");
});

it("maps wheel scrolling to the app capture with strict background arguments", async () => {
  const command = desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.wheel(image, { clientX: 220, clientY: 107.5, deltaY: -120 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("scroll_at", { windowId: 10, x: 240, y: 135, direction: "up", backgroundOnly: true, allowForegroundFallback: false }));
});

it("does not enable text input for an unconfirmed pointer fallback response", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return { inputMode: "pointer" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  expect(await screen.findByRole("alert")).toHaveTextContent(/does not support background interaction/);
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
});

it("holds app activation after a failed text update until the local draft is discarded", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "set_at") throw new Error("Text was not updated. This field is now read only.");
    return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Unapplied text" } });
  fireEvent.click(image, { clientX: 550, clientY: 250 });
  expect(await screen.findByRole("alert")).toHaveTextContent(/Text was not updated/);
  await waitFor(() => expect(screen.getByRole("button", { name: "Apply text to selected window" })).toBeEnabled());
  expect(input).toHaveValue("Unapplied text");
  expect(command.mock.calls.filter(([action]) => action === "interact")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Discard local draft" }));
  expect(input).toBeDisabled();
  fireEvent.click(image, { clientX: 550, clientY: 250 });
  await waitFor(() => expect(command.mock.calls.filter(([action]) => action === "interact")).toHaveLength(2));
});

it("applies a preserved local draft before activating an app control after reopening", async () => {
  const command = desktop();
  const order: string[] = [];
  let interacted = false;
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    order.push(action);
    if (action === "interact" && !interacted) { interacted = true; return { editable: true, value: "Existing note", inputMode: "accessibility" } as never; }
    if (action === "interact") return { activated: true, inputMode: "accessibility" } as never;
    return { updated: true, submitted: false, inputMode: "accessibility" } as never;
  });
  const props = { embedded: true, onClose: () => {}, onNotice: () => {} };
  const { rerender } = render(<DesktopPanel {...props} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(screen.getByLabelText("Type in selected window")).toBeEnabled());
  vi.useFakeTimers();
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "Preserved local draft" } });
  rerender(<DesktopPanel {...props} active={false} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  order.length = 0;
  rerender(<DesktopPanel {...props} />);
  await act(async () => { await Promise.resolve(); });
  fireEvent.click(image, { clientX: 550, clientY: 250 });
  await act(async () => { await Promise.resolve(); });
  expect(order).toEqual(["set_at", "interact"]);
  expect(command).toHaveBeenCalledWith("set_at", { windowId: 10, x: 480, y: 270, text: "Preserved local draft", backgroundOnly: true, allowForegroundFallback: false });
});
