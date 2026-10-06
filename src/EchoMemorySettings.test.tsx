import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EchoMemorySettings } from "./EchoMemorySettings";
import * as api from "./api";

let stored: api.EchoMemoryConfiguration;
beforeEach(() => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  stored = { memoryTokens: 4096, refreshTokens: 128, warmCacheMib: 128, activeWindowTokens: 32768 };
  vi.spyOn(api, "getEchoMemoryConfiguration").mockImplementation(async () => stored);
  vi.spyOn(api, "saveEchoMemoryConfiguration").mockImplementation(async configuration => {
    stored = configuration;
    return { configuration, applied: true };
  });
});
afterEach(() => { vi.restoreAllMocks(); Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); });

it("automatically saves bounded memory controls and explains when the runtime applies them", async () => {
  render(<EchoMemorySettings />);
  const recall = await screen.findByLabelText("Maximum active ECHO recall (tokens)");
  await waitFor(() => expect(recall).toBeEnabled());
  fireEvent.change(recall, { target: { value: "8192" } });
  fireEvent.change(screen.getByLabelText("Memory refresh interval (generated tokens)"), { target: { value: "256" } });
  await waitFor(() => expect(stored.memoryTokens).toBe(8192));
  expect(stored.refreshTokens).toBe(256);
  expect(await screen.findByText("Saved. Applies at the next memory refresh boundary.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Save ECHO settings" })).not.toBeInTheDocument();
});

it("retains invalid numeric drafts while other valid ECHO settings save", async () => {
  render(<EchoMemorySettings />);
  const cache = await screen.findByLabelText("ECHO RAM page cache (MiB)");
  await waitFor(() => expect(cache).toBeEnabled());
  fireEvent.change(cache, { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Maximum active ECHO recall (tokens)"), { target: { value: "8192" } });
  await waitFor(() => expect(stored.memoryTokens).toBe(8192));
  expect(stored.warmCacheMib).toBe(128);
  expect(cache).toHaveValue(null);
  expect(cache).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("RAM page cache");
});

it("retains a failed ECHO edit and offers a retry", async () => {
  vi.mocked(api.saveEchoMemoryConfiguration).mockRejectedValueOnce(new Error("Settings disk is unavailable"));
  render(<EchoMemorySettings />);
  const recall = await screen.findByLabelText("Maximum active ECHO recall (tokens)");
  await waitFor(() => expect(recall).toBeEnabled());
  fireEvent.change(recall, { target: { value: "8192" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Settings disk is unavailable");
  expect(recall).toHaveValue(8192);
  fireEvent.click(screen.getByRole("button", { name: "Retry saving ECHO settings" }));
  await waitFor(() => expect(stored.memoryTokens).toBe(8192));
});

it("does not claim that browser preview ECHO edits were persisted", async () => {
  Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
  render(<EchoMemorySettings />);
  const recall = await screen.findByLabelText("Maximum active ECHO recall (tokens)");
  await waitFor(() => expect(recall).toBeEnabled());
  fireEvent.change(recall, { target: { value: "8192" } });
  expect(screen.getByRole("status", { name: "ECHO settings save status" })).toHaveTextContent("not persisted");
  expect(stored.memoryTokens).toBe(4096);
});
