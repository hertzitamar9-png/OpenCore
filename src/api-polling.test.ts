import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";

const bridge = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: bridge.invoke }));
afterEach(() => { Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); bridge.invoke.mockReset(); });

it("shares pending polls per conversation and reads fresh data after completion", async () => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  let finish!: (rows: unknown[]) => void;
  bridge.invoke.mockImplementation((_command, { id }) => id === "slow"
    ? new Promise(resolve => { finish = resolve; }) : Promise.resolve([]));
  const first = api.conversation("slow");
  const second = api.conversation("slow");
  await api.conversation("other");
  expect(bridge.invoke).toHaveBeenCalledTimes(2);
  finish([]);
  await Promise.all([first, second]);
  bridge.invoke.mockResolvedValue([]);
  await api.conversation("slow");
  expect(bridge.invoke).toHaveBeenCalledTimes(3);
});

it("releases a failed background read so a later refresh can recover", async () => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  bridge.invoke.mockRejectedValueOnce(new Error("Temporary disk read failure")).mockResolvedValue([]);
  await expect(api.installedSkillModels()).rejects.toThrow("Temporary disk read failure");
  await expect(api.installedSkillModels()).resolves.toEqual([]);
  expect(bridge.invoke).toHaveBeenCalledTimes(2);
});

it("reads a committed final response after an older pending poll instead of reusing it", async () => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  let finishOld!: (rows: unknown[]) => void;
  const saved = [{ id: 12, content: "The completed answer" }];
  bridge.invoke.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; })).mockResolvedValue(saved);
  const poll = api.conversation("checkpoint");
  const final = api.conversation("checkpoint", { fresh: true });
  expect(bridge.invoke).toHaveBeenCalledTimes(1);
  finishOld([]);
  await expect(poll).resolves.toEqual([]);
  await expect(final).resolves.toEqual(saved);
  expect(bridge.invoke).toHaveBeenCalledTimes(2);
});

it("refreshes a changed runtime after a pending pre-action snapshot", async () => {
  Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  let finishOld!: (value: unknown) => void;
  bridge.invoke.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValue({ runtime: { status: "running" } });
  const poll = api.snapshot();
  const afterStart = api.snapshot({ fresh: true });
  expect(bridge.invoke).toHaveBeenCalledTimes(1);
  finishOld({ runtime: { status: "stopped" } });
  await expect(poll).resolves.toHaveProperty("runtime.status", "stopped");
  await expect(afterStart).resolves.toHaveProperty("runtime.status", "running");
  expect(bridge.invoke).toHaveBeenCalledTimes(2);
});
