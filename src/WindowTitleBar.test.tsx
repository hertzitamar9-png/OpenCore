import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
});
