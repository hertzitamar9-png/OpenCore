import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectActionsMenu } from "./ProjectActionsMenu";
import type { ProjectSummary } from "./types";

const project: ProjectSummary = {
  id: "work", name: "Work", folderPath: "C:\\work", needsFolder: false, folderAvailable: true,
  createdAt: "2026", updatedAt: "2026", conversationCount: 2,
};

describe("ProjectActionsMenu", () => {
  it("renders outside scroll containment, focuses actions, and closes with Escape", () => {
    const anchor = document.createElement("button");
    document.body.append(anchor);
    const onClose = vi.fn();
    try {
      render(<div style={{ overflow: "hidden" }}><ProjectActionsMenu project={project} anchor={anchor} onClose={onClose} onOpenFolder={vi.fn()} onChangeFolder={vi.fn()} onRename={vi.fn()} onRemove={vi.fn()} /></div>);
      const menu = screen.getByRole("menu", { name: "Work project actions" });
      expect(menu.parentElement).toBe(document.body);
      expect(screen.getByRole("menuitem", { name: "Open folder" })).toHaveFocus();
      expect(menu).toHaveStyle({ position: "fixed" });
      fireEvent.keyDown(menu, { key: "Escape" });
      expect(onClose).toHaveBeenCalled();
    } finally { anchor.remove(); }
  });

  it("reports folder-opening failures at the menu instead of failing silently", async () => {
    const anchor = document.createElement("button");
    document.body.append(anchor);
    try {
      render(<ProjectActionsMenu project={project} anchor={anchor} onClose={vi.fn()} onOpenFolder={vi.fn().mockRejectedValue(new Error("Explorer unavailable"))} onChangeFolder={vi.fn()} onRename={vi.fn()} onRemove={vi.fn()} />);
      fireEvent.click(screen.getByRole("menuitem", { name: "Open folder" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Explorer unavailable");
    } finally { anchor.remove(); }
  });

  it("clamps at the viewport edge and supports arrows and outside click", () => {
    const anchor = document.createElement("button");
    anchor.getBoundingClientRect = () => ({ x: 1000, y: 740, left: 1000, right: 1020, top: 740, bottom: 760, width: 20, height: 20, toJSON: () => ({}) });
    document.body.append(anchor);
    const onClose = vi.fn();
    try {
      render(<ProjectActionsMenu project={project} anchor={anchor} onClose={onClose} onOpenFolder={vi.fn()} onChangeFolder={vi.fn()} onRename={vi.fn()} onRemove={vi.fn()} />);
      const menu = screen.getByRole("menu", { name: "Work project actions" });
      expect(Number.parseInt(menu.style.left)).toBeLessThan(1000);
      expect(Number.parseInt(menu.style.top)).toBeLessThan(740);
      fireEvent.keyDown(menu, { key: "ArrowDown" });
      expect(screen.getByRole("menuitem", { name: "Change folder" })).toHaveFocus();
      fireEvent.pointerDown(document.body);
      expect(onClose).toHaveBeenCalledOnce();
    } finally { anchor.remove(); }
  });

  it("does not offer a missing folder as an openable destination", () => {
    const anchor = document.createElement("button");
    document.body.append(anchor);
    try {
      render(<ProjectActionsMenu project={{ ...project, folderAvailable: false }} anchor={anchor} onClose={vi.fn()} onOpenFolder={vi.fn()} onChangeFolder={vi.fn()} onRename={vi.fn()} onRemove={vi.fn()} />);
      expect(screen.getByRole("menuitem", { name: "Open folder" })).toBeDisabled();
      expect(screen.getByText(/Folder unavailable/)).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: "Change folder" })).toBeEnabled();
    } finally { anchor.remove(); }
  });
});
