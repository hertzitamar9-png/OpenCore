import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { open } from "@tauri-apps/plugin-dialog";
import { AgentConnectorControls } from "./AgentConnectorControls";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
afterEach(() => vi.clearAllMocks());

it("stores the chosen real profile folder and uses it for Hermes configuration", async () => {
  const folder = "C:\\Users\\test\\AppData\\Local\\hermes\\profiles\\coder";
  vi.mocked(open).mockResolvedValue(folder);
  const select = vi.fn().mockResolvedValue("Source selected"), configure = vi.fn().mockResolvedValue("Profile installed");
  const sync = vi.fn().mockResolvedValue({ id: "job-1" }), result = vi.fn();
  render(<AgentConnectorControls id="hermes" onSelectFolder={select} onConfigure={configure} onSync={sync} onResult={result} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose history folder" }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("hermes", folder));
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ directory: true, multiple: false }));
  await waitFor(() => expect(screen.getByText(folder)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Connect OpenCore" }));
  await waitFor(() => expect(configure).toHaveBeenCalledWith("hermes", folder));
  await waitFor(() => expect(result).toHaveBeenCalledWith("Profile installed"));
  fireEvent.click(screen.getByRole("button", { name: "Sync chats and projects" }));
  await waitFor(() => expect(sync).toHaveBeenCalledWith("hermes"));
});

it("does not report or persist a folder when the native picker is cancelled", async () => {
  vi.mocked(open).mockResolvedValue(null);
  const select = vi.fn(), result = vi.fn();
  render(<AgentConnectorControls id="opencode" onSelectFolder={select} onConfigure={vi.fn()} onSync={vi.fn()} onResult={result} />);
  fireEvent.click(screen.getByRole("button", { name: "Choose history folder" }));
  await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  expect(select).not.toHaveBeenCalled(); expect(result).not.toHaveBeenCalled();
});

it("locks actions during an import and reports native source errors", async () => {
  let reject!: (error: Error) => void;
  const sync = vi.fn().mockImplementation(() => new Promise((_, failure) => { reject = failure; }));
  const clear = vi.fn(), error = vi.fn();
  render(<AgentConnectorControls id="opencode" onSelectFolder={vi.fn()} onConfigure={vi.fn()} onSync={sync} onClear={clear} onError={error} />);
  fireEvent.click(screen.getByRole("button", { name: "Sync chats and projects" }));
  expect(screen.getByRole("button", { name: "Clear imported" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Choose history folder" })).toBeDisabled();
  await act(async () => reject(new Error("Close OpenCode before importing its database")));
  expect(screen.getByRole("alert")).toHaveTextContent("Close OpenCode");
  expect(clear).not.toHaveBeenCalled(); expect(error).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Clear imported" }));
  expect(clear).toHaveBeenCalledWith("opencode");
});
