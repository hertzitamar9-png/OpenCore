import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectPicker } from "./ProjectPicker";
import * as api from "./api";
import type { ProjectSummary } from "./types";

const projects: ProjectSummary[] = [
  { id: "a", name: "App", folderPath: "C:\\work\\alpha\\app", needsFolder: false, folderAvailable: true, createdAt: "2026", updatedAt: "2026", conversationCount: 2 },
  { id: "b", name: "App", folderPath: "C:\\work\\beta\\app", needsFolder: false, folderAvailable: true, createdAt: "2026", updatedAt: "2026", conversationCount: 1 },
  { id: "legacy", name: "Old", folderPath: null, needsFolder: true, folderAvailable: false, createdAt: "2026", updatedAt: "2026", conversationCount: 1 },
];

afterEach(() => vi.restoreAllMocks());

describe("ProjectPicker", () => {
  it("distinguishes same-named folders by path and excludes unresolved projects", () => {
    const onChange = vi.fn();
    render(<ProjectPicker value={null} projects={projects} onChange={onChange} onCreate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Project: No project" }));
    const choices = screen.getAllByRole("menuitemradio", { name: /App/ });
    expect(choices).toHaveLength(2);
    expect(choices[0]).toHaveTextContent("C:\\work\\alpha\\app");
    expect(choices[1]).toHaveTextContent("C:\\work\\beta\\app");
    expect(screen.queryByRole("menuitemradio", { name: /Old/ })).not.toBeInTheDocument();
    fireEvent.click(choices[1]);
    expect(onChange).toHaveBeenCalledWith("b");
  });

  it("excludes projects whose linked folder is currently unavailable", () => {
    render(<ProjectPicker value={null} projects={[...projects, { ...projects[0], id: "missing", name: "Missing", folderAvailable: false }]} onChange={vi.fn()} onCreate={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Project: No project" }));
    expect(screen.queryByRole("menuitemradio", { name: /Missing/ })).not.toBeInTheDocument();
  });

  it("requires the native folder selection before creating", async () => {
    vi.spyOn(api, "chooseProjectFolder").mockResolvedValue("C:\\work\\new");
    const onCreate = vi.fn().mockResolvedValue(true);
    render(<ProjectPicker value={null} projects={projects} onChange={vi.fn()} onCreate={onCreate} />);
    fireEvent.click(screen.getByRole("button", { name: "Project: No project" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Create project" }));
    fireEvent.change(screen.getByRole("textbox", { name: "New project name" }), { target: { value: "Research" } });
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
    expect(await screen.findByText("C:\\work\\new")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("Research", "C:\\work\\new"));
  });
});
