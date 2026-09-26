import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { NativeBrowserPanel } from "./NativeBrowserPanel";

afterEach(() => vi.restoreAllMocks());

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
