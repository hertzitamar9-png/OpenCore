import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { DesktopPanel as NativeDesktopPanel } from "./DesktopPanel";
const DesktopPanel = (props: Parameters<typeof NativeDesktopPanel>[0]) => <NativeDesktopPanel initialMode="background" {...props} />;

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
beforeEach(() => {
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key),
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("lets the user choose both control modes and remembers the choice", async () => {
  const command = desktop();
  const view = render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  expect(screen.getByLabelText("Computer control mode")).toHaveValue("direct");
  fireEvent.change(screen.getByLabelText("Computer control mode"), { target: { value: "background" } });
  const image = await selectWindow(); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("interact", expect.objectContaining({ backgroundOnly: true, manualControl: true, allowForegroundFallback: false })));
  view.unmount();
  render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  expect(screen.getByLabelText("Computer control mode")).toHaveValue("background");
});

it("sends real manual clicks and text without the background-only restriction", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "click") return { activated: true, inputMode: "pointer" } as never;
    return { updated: true, submitted: false, inputMode: "pointer" } as never;
  });
  render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("click", { windowId: 10, x: 480, y: 270, directControl: true }));
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "Manual input works" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("commit_text", { windowId: 10, x: 480, y: 270, text: "Manual input works", submit: false, directControl: true }));
  expect(command.mock.calls.some(([action]) => action === "set_at")).toBe(false);
});

it("clears activity when the computer panel is closed", async () => {
  const command = desktop();
  const view = render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  await screen.findByLabelText("Window");
  view.unmount();
  expect(command).toHaveBeenCalledWith("clear_activity");
});

it("explains the app permission wait and captures only after that app is allowed", async () => {
  let allowed = false;
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => action === "list"
    ? { windows: [{ ...windows[1], application: "Notes.exe", permission: allowed ? "allow" : "ask" }] } as never
    : screenshot(Number(args.windowId)) as never);
  const grant = vi.spyOn(api, "allowComputerWindow").mockImplementation(async () => { allowed = true; return {} as api.ComputerAccess; });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const picker = await screen.findByLabelText("Window");
  await screen.findByRole("option", { name: "Notes" });
  fireEvent.change(picker, { target: { value: "10" } });
  expect(await screen.findByText("Waiting for app permission")).toBeVisible();
  expect(screen.queryByText("Capturing selected window…")).toBeNull();
  expect(command.mock.calls.some(([action]) => action === "screenshot")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Allow this app" }));
  expect(await screen.findByAltText("Selected Windows app")).toBeVisible();
  expect(grant).toHaveBeenCalledWith(10);
});

it("shows a capture failure instead of an endless capturing message", async () => {
  const command = desktop();
  command.mockImplementation(async action => {
    if (action === "list") return { windows } as never;
    throw new Error("The selected window is minimized.");
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  await screen.findByRole("option", { name: "Notes" });
  fireEvent.change(screen.getByLabelText("Window"), { target: { value: "10" } });
  expect(await screen.findByText("Capture unavailable")).toBeVisible();
  expect(screen.queryByText("Capturing selected window…")).toBeNull();
  expect(screen.getByRole("alert")).toHaveTextContent("The selected window is minimized.");
  expect(screen.getByRole("button", { name: "Refresh capture" })).toBeEnabled();
});

it("starts disabled and enables computer use with one click while preserving saved app permissions", async () => {
  const policy: api.ComputerAccess = { enabled: false, revision: 4, apps: [{ path: "C:\\Apps\\Notes.exe", name: "Notes", access: "allow" }] };
  let enabled = false;
  const command = desktop();
  command.mockImplementation(async action => ({ windows: enabled ? [windows[1]] : [], computerUseEnabled: enabled }) as never);
  vi.spyOn(api, "computerAccess").mockResolvedValue(policy);
  const save = vi.spyOn(api, "setComputerAccess").mockImplementation(async next => {
    enabled = next.enabled;
    return { ...next, revision: 5 };
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const enable = await screen.findByRole("button", { name: "Enable computer use" });
  expect(screen.getByText("Computer use is disabled")).toBeVisible();
  expect(screen.queryByAltText("Selected Windows app")).toBeNull();
  fireEvent.click(enable);
  await waitFor(() => expect(save).toHaveBeenCalledWith({ ...policy, enabled: true }));
  expect(save).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Only permitted apps can be controlled")).toBeVisible();
  await waitFor(() => expect(screen.getByRole("option", { name: "Notes" })).toBeInTheDocument());
  expect(command.mock.calls.some(([action]) => action === "screenshot")).toBe(false);
});

it("clears the selected capture when computer access is disabled during refresh", async () => {
  const command = desktop();
  let enabled = true;
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows: enabled ? windows : [], computerUseEnabled: enabled } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  await selectWindow();
  const before = command.mock.calls.filter(([action]) => action === "screenshot").length;
  enabled = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh capture" }));
  await waitFor(() => expect(screen.queryByAltText("Selected Windows app")).toBeNull());
  expect(screen.getByText("Computer use is disabled")).toBeVisible();
  expect(command.mock.calls.filter(([action]) => action === "screenshot")).toHaveLength(before);
});

it("asks permission before capturing an unknown executable", async () => {
  const command = desktop();
  let allowed = false;
  command.mockImplementation(async action => action === "list"
    ? { windows: [{ ...windows[1], permission: allowed ? "allowed" : "ask" }], computerUseEnabled: true } as never : screenshot(10) as never);
  const grant = vi.spyOn(api, "allowComputerWindow").mockImplementation(async () => {
    allowed = true;
    return { enabled: true, apps: [{ path: "C:\\Apps\\Notes.exe", name: "Notes", access: "allow" }] };
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const picker = await screen.findByLabelText("Window");
  await waitFor(() => expect(picker.querySelector('option[value="10"]')).not.toBeNull());
  fireEvent.change(picker, { target: { value: "10" } });
  expect(await screen.findByRole("button", { name: "Allow this app" })).toBeVisible();
  expect(screen.queryByAltText("Selected Windows app")).toBeNull();
  expect(command.mock.calls.some(([action]) => action === "screenshot")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Allow this app" }));
  expect(await screen.findByAltText("Selected Windows app")).toBeVisible();
  expect(grant).toHaveBeenCalledWith(10);
  expect(grant).toHaveBeenCalledTimes(1);
});

it("retains the selected app and draft when the window list temporarily omits a live capture", async () => {
  const command = desktop();
  let omitted = false;
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows: omitted ? windows.filter(item => item.windowId !== 10) : windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  vi.useFakeTimers();
  fireEvent.change(input, { target: { value: "Keep my unfinished draft" } });
  omitted = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh capture" }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByLabelText("Window")).toHaveValue("10");
  expect(screen.getByRole("option", { name: "Notes" })).toBeInTheDocument();
  expect(input).toHaveValue("Keep my unfinished draft");
  expect(screen.getByAltText("Selected Windows app")).toBeVisible();
  expect(screen.queryByText(/selected window closed/i)).toBeNull();
});

it("blocks background controls during capture failure and recovers without losing the local draft", async () => {
  const command = desktop();
  let unavailable = false;
  let targetValue = "Existing note";
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") {
      if (unavailable) throw new Error("Window capture is temporarily unavailable");
      return screenshot(Number(args.windowId)) as never;
    }
    if (action === "interact") return { editable: true, value: targetValue, inputMode: "accessibility" } as never;
    targetValue = String(args.text);
    return { updated: true, submitted: false, inputMode: "accessibility" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  vi.useFakeTimers();
  fireEvent.change(input, { target: { value: "Draft before capture failure" } });
  unavailable = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh capture" }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole("alert")).toHaveTextContent(/temporarily unavailable/);
  expect(screen.getByLabelText("Window")).toHaveValue("10");
  expect(input).toHaveValue("Draft before capture failure");
  expect(screen.getByAltText("Selected Windows app")).toHaveAttribute("aria-disabled", "true");
  expect(screen.getByRole("button", { name: "Apply text to selected window" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "Draft while capture is unavailable" } });
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await act(async () => { await vi.advanceTimersByTimeAsync(200); });
  expect(targetValue).toBe("Existing note");
  expect(input).toHaveValue("Draft while capture is unavailable");
  unavailable = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh capture" }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Apply text to selected window" })).toBeEnabled();
  expect(input).toHaveValue("Draft while capture is unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await act(async () => { await Promise.resolve(); });
  expect(targetValue).toBe("Draft while capture is unavailable");
});

it("maps screenshot clicks to the selected window and requires background control for every text update", async () => {
  const command = desktop();
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(screen.getByLabelText("Type in selected window")).toHaveValue("Existing note"));
  expect(command).toHaveBeenCalledWith("interact", { windowId: 10, x: 480, y: 270, backgroundOnly: true, allowForegroundFallback: false, manualControl: true });
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "New note" } });
  await waitFor(() => expect(command).toHaveBeenCalledWith("set_at", { windowId: 10, x: 480, y: 270, text: "New note", backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
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

it("controls the real desktop through the explicitly selected direct mode", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "click") return { activated: true, inputMode: "pointer" } as never;
    return { updated: true, submitted: false, inputMode: "pointer" } as never;
  });
  render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(0); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("click", { windowId: 0, x: 960, y: 540, directControl: true }));
  fireEvent.change(screen.getByLabelText("Type in selected window"), { target: { value: "Real desktop field" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("commit_text", {
    windowId: 0, x: 960, y: 540, text: "Real desktop field", submit: false, directControl: true,
  }));
  expect(screen.queryByText(/Entire desktop is view only/)).toBeNull();
});

it("asks for the actual app under a desktop click without replaying the click after permission", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return { permissionRequired: true, permissionWindowId: 20, application: "Calculator" } as never;
  });
  const grant = vi.spyOn(api, "allowComputerWindow").mockResolvedValue({ enabled: true, apps: [], revision: 1 });
  render(<NativeDesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(0); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  fireEvent.click(await screen.findByRole("button", { name: "Allow Calculator" }));
  await waitFor(() => expect(grant).toHaveBeenCalledWith(20));
  expect(command.mock.calls.filter(([action]) => action === "click")).toHaveLength(1);
  expect(screen.getByLabelText("Window")).toHaveValue("0");
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
  expect(command).toHaveBeenCalledWith("commit_text", { windowId: 10, x: 480, y: 270, text: "Keep this draft", backgroundOnly: true, allowForegroundFallback: false, manualControl: true });
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
  expect(await screen.findByRole("alert")).toHaveTextContent("This control needs Direct control");
  expect(notice).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
  expect(command.mock.calls.filter(([action]) => action !== "list" && action !== "screenshot").map(([action]) => action)).toEqual(["interact"]);
});

it("offers direct control for an unsupported background control without sending input until the user clicks again", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "interact") throw new Error("This control does not expose background interaction. Foreground pointer and keyboard input are disabled; use the control in the application.");
    return { activated: true, inputMode: "pointer" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const switchMode = await screen.findByRole("button", { name: "Switch to Direct control" });
  expect(screen.getByRole("alert")).toHaveTextContent("This control needs Direct control. The app will come forward briefly for input.");
  fireEvent.click(switchMode);
  expect(screen.getByLabelText("Computer control mode")).toHaveValue("direct");
  expect(command.mock.calls.some(([action]) => action === "click")).toBe(false);
  const refreshed = await screen.findByAltText("Selected Windows app"); imageBounds(refreshed);
  fireEvent.click(refreshed, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("click", { windowId: 10, x: 480, y: 270, directControl: true }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("lets the user dismiss a computer error inside the panel", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    throw new Error("The selected app is no longer available.");
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss computer message" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("preserves a completed activation and surfaces desktop-change warnings without inviting a retry", async () => {
  const command = desktop();
  const warning = "Desktop focus changed during this action. Check the completed result before retrying.";
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return { activated: true, scrolled: true, backgroundVerified: false, warning: { message: warning } } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  expect((await screen.findByText(new RegExp(warning))).closest(".desktop-feedback")).toHaveClass("desktop-feedback-warning");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(command.mock.calls.filter(([action]) => action === "interact")).toHaveLength(1);
  fireEvent.wheel(image, { clientX: 220, clientY: 107.5, deltaY: -120 });
  await waitFor(() => expect(command.mock.calls.filter(([action]) => action === "scroll_at")).toHaveLength(1));
  expect(screen.getByText(new RegExp(warning))).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("shows successful text-application verification warnings while keeping the applied draft", async () => {
  const command = desktop();
  const warning = "Desktop cursor changed during this action. Check the completed result before retrying.";
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "interact") return { editable: true, value: "Existing note" } as never;
    return { updated: true, submitted: false, backgroundVerified: false, warning: { message: warning } } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Applied note" } });
  expect(await screen.findByText(new RegExp(warning))).toBeVisible();
  expect(command.mock.calls.filter(([action]) => action === "set_at")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await waitFor(() => expect(command.mock.calls.filter(([action]) => action === "commit_text")).toHaveLength(1));
  expect(screen.getByText(new RegExp(warning))).toBeVisible();
  expect(input).toHaveValue("Applied note");
  expect(screen.queryByRole("alert")).toBeNull();
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
  await waitFor(() => expect(command).toHaveBeenCalledWith("screenshot", { windowId: 10, backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
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
  await waitFor(() => expect(command).toHaveBeenCalledWith("scroll_at", { windowId: 10, x: 240, y: 135, direction: "up", backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
});

it("adds the capture crop origin to clicks and wheel actions, including a refreshed origin", async () => {
  const command = desktop();
  let origin = { x: 8, y: 7 };
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return { ...screenshot(Number(args.windowId)), origin } as never;
    return { activated: true, scrolled: true, inputMode: "accessibility" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("interact", { windowId: 10, x: 488, y: 277, backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh capture" })).toBeEnabled());
  fireEvent.wheel(image, { clientX: 220, clientY: 107.5, deltaY: -120 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("scroll_at", { windowId: 10, x: 248, y: 142, direction: "up", backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh capture" })).toBeEnabled());
  origin = { x: 0, y: 0 };
  const capturesBeforeRefresh = command.mock.calls.filter(([action]) => action === "screenshot").length;
  fireEvent.click(screen.getByRole("button", { name: "Refresh capture" }));
  await waitFor(() => expect(command.mock.calls.filter(([action]) => action === "screenshot").length).toBeGreaterThan(capturesBeforeRefresh));
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  await waitFor(() => expect(command).toHaveBeenCalledWith("interact", { windowId: 10, x: 480, y: 270, backgroundOnly: true, allowForegroundFallback: false, manualControl: true }));
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
  expect(await screen.findByRole("alert")).toHaveTextContent(/needs Direct control/);
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
});

it.each([
  { activated: true, inputMode: "pointer" },
  { editable: true, value: "Foreground field", inputMode: "keyboard" },
])("rejects confirmed foreground input from a background control response: $inputMode", async result => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    return result as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  expect(await screen.findByRole("alert")).toHaveTextContent(/needs Direct control/);
  expect(screen.getByLabelText("Type in selected window")).toBeDisabled();
  expect(command.mock.calls.filter(([action]) => action === "interact")).toHaveLength(1);
});

it("keeps an unapplied draft and blocks activation when a text response reports no update", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "interact") return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
    return { updated: false, submitted: false, message: "This field does not support background text updates." } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow();
  imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Keep this unapplied text" } });
  expect(await screen.findByRole("alert")).toHaveTextContent(/needs Direct control/);
  expect(input).toHaveValue("Keep this unapplied text");
  expect(screen.getByRole("button", { name: "Discard local draft" })).toBeVisible();
  fireEvent.click(image, { clientX: 550, clientY: 250 });
  await waitFor(() => expect(screen.getByRole("button", { name: "Apply text to selected window" })).toBeEnabled());
  expect(command.mock.calls.filter(([action]) => action === "interact")).toHaveLength(1);
});

it("keeps an unapplied draft when the user switches to direct control", async () => {
  const command = desktop();
  command.mockImplementation(async (action, args = {}) => {
    if (action === "list") return { windows } as never;
    if (action === "screenshot") return screenshot(Number(args.windowId)) as never;
    if (action === "interact") return { editable: true, value: "Existing note", inputMode: "accessibility" } as never;
    if (action === "set_at") throw new Error("This field does not support background text updates.");
    return { updated: true, inputMode: "pointer" } as never;
  });
  render(<DesktopPanel embedded onClose={() => {}} onNotice={() => {}} />);
  const image = await selectWindow(); imageBounds(image);
  fireEvent.click(image, { clientX: 340, clientY: 175 });
  const input = screen.getByLabelText("Type in selected window");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "Preserved draft" } });
  fireEvent.click(await screen.findByRole("button", { name: "Switch to Direct control" }));
  expect(input).toHaveValue("Preserved draft");
  expect(input).toBeEnabled();
  await screen.findByAltText("Selected Windows app");
  fireEvent.click(screen.getByRole("button", { name: "Apply text to selected window" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("commit_text", {
    windowId: 10, x: 480, y: 270, text: "Preserved draft", submit: false, directControl: true,
  }));
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
  expect(command).toHaveBeenCalledWith("set_at", { windowId: 10, x: 480, y: 270, text: "Preserved local draft", backgroundOnly: true, allowForegroundFallback: false, manualControl: true });
});
