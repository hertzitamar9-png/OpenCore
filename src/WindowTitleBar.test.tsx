import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WindowTitleBar } from "./WindowTitleBar";

const windowApi = vi.hoisted(() => ({
  isMaximized: vi.fn(),
  onResized: vi.fn(),
  startDragging: vi.fn(),
  minimize: vi.fn(),
  toggleMaximize: vi.fn(),
  close: vi.fn(),
}));

vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => windowApi }));

describe("WindowTitleBar", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.style.removeProperty("--ui-scale");
  });
  beforeEach(() => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
    windowApi.isMaximized.mockResolvedValue(false);
    windowApi.onResized.mockResolvedValue(() => {});
    windowApi.startDragging.mockResolvedValue(undefined);
    windowApi.minimize.mockResolvedValue(undefined);
    windowApi.toggleMaximize.mockResolvedValue(undefined);
    windowApi.close.mockResolvedValue(undefined);
    windowApi.toggleMaximize.mockClear();
  });

  it("maximizes from the square button with one click and ignores titlebar double-clicks", () => {
    render(<WindowTitleBar />);

    const maximize = screen.getByRole("button", { name: "Maximize OpenCore" });
    expect(maximize).toHaveClass("window-maximize");
    fireEvent.click(maximize);
    expect(windowApi.toggleMaximize).toHaveBeenCalledTimes(1);

    const titlebar = screen.getByRole("banner");
    expect(titlebar).not.toHaveAttribute("data-tauri-drag-region");
    fireEvent.doubleClick(titlebar);
    expect(windowApi.toggleMaximize).toHaveBeenCalledTimes(1);
  });

  it("uses the restore icon after a native maximize event", async () => {
    let onResize: () => void = () => {};
    windowApi.onResized.mockImplementation(async (callback: () => void) => { onResize = callback; return () => {}; });
    render(<WindowTitleBar />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Maximize OpenCore" })).toBeVisible());
    windowApi.isMaximized.mockResolvedValue(true);
    await act(async () => { onResize(); });
    expect(screen.getByRole("button", { name: "Restore OpenCore" }).querySelector(".lucide-copy")).not.toBeNull();
  });

  it("scales the interface during live resizes without changing the saved text size", async () => {
    vi.stubGlobal("innerWidth", 1280);
    vi.stubGlobal("innerHeight", 720);
    const { container, unmount } = render(<div style={{ "--chat-font-size": "19px" } as React.CSSProperties}><WindowTitleBar /></div>);
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--ui-scale")).toBe("1"));
    vi.stubGlobal("innerWidth", 1920);
    vi.stubGlobal("innerHeight", 1080);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--ui-scale")).toBe("1.12"));
    expect(container.firstElementChild?.getAttribute("style")).toContain("--chat-font-size: 19px");
    vi.stubGlobal("innerWidth", 480);
    vi.stubGlobal("innerHeight", 320);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--ui-scale")).toBe("0.94"));
    vi.stubGlobal("innerWidth", 7680);
    vi.stubGlobal("innerHeight", 4320);
    fireEvent(window, new Event("resize"));
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--ui-scale")).toBe("1.2"));
    unmount();
    expect(document.documentElement.style.getPropertyValue("--ui-scale")).toBe("");
  });
});
