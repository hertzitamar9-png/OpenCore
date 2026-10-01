import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { ModelDeleteDialog } from "./ModelDeleteDialog";

const model: api.InstalledModel = { id: "echo", label: "ECHO 3T", description: "Chat", precision: "BF16",
  contextTokens: 32768, license: "Apache", experimental: false, note: "Local", selectable: true,
  installed: true, externalManaged: false, downloadBytes: 0, totalBytes: 100 };
const file: api.ModelRemovalFile = { path: "C:\\OpenCore\\models\\echo.gguf", bytes: 100, external: false, sharedWith: [] };
const plan: api.ModelRemovalPlan = { modelId: model.id, label: model.label, files: [file],
  retainedFiles: [{ ...file, path: "C:\\OpenCore\\models\\shared.gguf", sharedWith: ["Native 1M"] }],
  totalBytes: 100, confirmationToken: "reviewed-file-token" };
afterEach(() => vi.restoreAllMocks());

it("shows the reviewed files and shared files, then sends the exact plan only after confirmation", async () => {
  vi.spyOn(api, "modelRemovalPlan").mockResolvedValue(plan);
  const remove = vi.fn().mockResolvedValue(undefined);
  render(<ModelDeleteDialog model={model} runtimeActive={false} onCancel={vi.fn()} onDelete={remove} />);
  expect(await screen.findByText(file.path)).toBeInTheDocument();
  expect(screen.getByText("1 shared files will be kept")).toBeInTheDocument();
  expect(screen.getByText("Used by Native 1M")).toBeInTheDocument();
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Delete model" }));
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
  expect(remove).toHaveBeenCalledWith(plan);
});

it("requires explicit acceptance for external checkpoint files", async () => {
  vi.spyOn(api, "modelRemovalPlan").mockResolvedValue({ ...plan, files: [{ ...file, external: true }] });
  const remove = vi.fn().mockResolvedValue(undefined);
  render(<ModelDeleteDialog model={model} runtimeActive={false} onCancel={vi.fn()} onDelete={remove} />);
  const acceptance = await screen.findByRole("checkbox");
  expect(screen.getByRole("button", { name: "Delete model" })).toBeDisabled();
  fireEvent.click(acceptance);
  expect(screen.getByRole("button", { name: "Delete model" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Delete model" }));
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
});

it("disables deletion when no local files exist and allows Escape to cancel", async () => {
  vi.spyOn(api, "modelRemovalPlan").mockResolvedValue({ ...plan, files: [], totalBytes: 0 });
  const cancel = vi.fn();
  const remove = vi.fn();
  render(<ModelDeleteDialog model={model} runtimeActive={false} onCancel={cancel} onDelete={remove} />);
  expect(await screen.findByText("This model has no local files to delete.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete model" })).toBeDisabled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(remove).not.toHaveBeenCalled();
});

it("requires a new review after a deletion failure", async () => {
  const review = vi.spyOn(api, "modelRemovalPlan").mockResolvedValue(plan);
  const remove = vi.fn().mockRejectedValueOnce(new Error("Model files changed"));
  render(<ModelDeleteDialog model={model} runtimeActive={true} onCancel={vi.fn()} onDelete={remove} />);
  const confirm = await screen.findByRole("button", { name: "Stop runtime and delete" });
  expect(screen.getByText(/cancel any active generation/)).toBeInTheDocument();
  fireEvent.click(confirm);
  expect(await screen.findByRole("alert")).toHaveTextContent("Model files changed");
  expect(screen.getByRole("button", { name: "Delete model" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Check files again" }));
  expect(await screen.findByRole("button", { name: "Stop runtime and delete" })).toBeEnabled();
  expect(review).toHaveBeenCalledTimes(2);
  expect(remove).toHaveBeenCalledTimes(1);
});
