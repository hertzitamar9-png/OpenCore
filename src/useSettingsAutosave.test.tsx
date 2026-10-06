import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { serializeSettingsSave, SETTINGS_SAVE_ERROR_EVENT, useSettingsAutosave, waitForSettingsSaves } from "./useSettingsAutosave";

afterEach(() => vi.useRealTimers());

it("flushes a debounced edit before navigation can read the stored settings", async () => {
  let persisted = 1;
  const { rerender, unmount } = renderHook(({ value }) => useSettingsAutosave({
    value, savedValue: 1,
    save: next => serializeSettingsSave("navigation-test", async () => { persisted = next; return next; }),
  }), { initialProps: { value: 1 } });
  rerender({ value: 2 });
  unmount();
  await waitForSettingsSaves("navigation-test");
  expect(persisted).toBe(2);
});

it("waits for another edit queued while a navigation read is already waiting", async () => {
  let releaseFirst!: () => void, releaseSecond!: () => void;
  let readCompleted = false, persisted = 1;
  const first = serializeSettingsSave("navigation-queue-test", async () => {
    await new Promise<void>(resolve => { releaseFirst = resolve; });
    persisted = 2;
  });
  await Promise.resolve();
  await Promise.resolve();
  const read = waitForSettingsSaves("navigation-queue-test").then(() => { readCompleted = true; });
  const second = serializeSettingsSave("navigation-queue-test", async () => {
    await new Promise<void>(resolve => { releaseSecond = resolve; });
    persisted = 3;
  });
  releaseFirst();
  await first;
  await Promise.resolve();
  await Promise.resolve();
  expect(readCompleted).toBe(false);
  releaseSecond();
  await Promise.all([read, second]);
  expect(persisted).toBe(3);
});

it("keeps Saving visible until the newest queued edit is persisted", async () => {
  vi.useFakeTimers();
  let persisted = 1;
  const releases: (() => void)[] = [];
  const { result, rerender, unmount } = renderHook(({ value }) => useSettingsAutosave({
    value, savedValue: 1,
    save: next => serializeSettingsSave("race-test", async () => {
      await new Promise<void>(resolve => releases.push(resolve));
      persisted = next;
      return next;
    }),
  }), { initialProps: { value: 1 } });
  rerender({ value: 2 });
  await act(() => vi.advanceTimersByTimeAsync(450));
  rerender({ value: 3 });
  await act(() => vi.advanceTimersByTimeAsync(450));
  expect(releases).toHaveLength(1);
  await act(async () => { releases[0](); await vi.advanceTimersByTimeAsync(0); });
  expect(persisted).toBe(2);
  expect(releases).toHaveLength(2);
  expect(result.current.status).toBe("saving");
  await act(async () => { releases[1](); await vi.advanceTimersByTimeAsync(0); });
  expect(persisted).toBe(3);
  expect(result.current.status).toBe("saved");
  unmount();
});

it("saves a return to the original value after an earlier write started", async () => {
  vi.useFakeTimers();
  let persisted = 1;
  let release!: () => void;
  let first = true;
  const { rerender, unmount } = renderHook(({ value }) => useSettingsAutosave({
    value, savedValue: 1,
    save: next => serializeSettingsSave("revert-test", async () => {
      if (first) { first = false; await new Promise<void>(resolve => { release = resolve; }); }
      persisted = next;
      return next;
    }),
  }), { initialProps: { value: 1 } });
  rerender({ value: 2 });
  await act(() => vi.advanceTimersByTimeAsync(450));
  rerender({ value: 1 });
  unmount();
  release();
  await waitForSettingsSaves("revert-test");
  expect(persisted).toBe(1);
});

it("reports a failed write without an endless retry and allows an explicit retry", async () => {
  let writes = 0, persisted = 1;
  const { result, rerender, unmount } = renderHook(({ value }) => useSettingsAutosave({
    value, savedValue: 1,
    save: async next => { if (++writes === 1) throw new Error("Disk is full"); persisted = next; return next; },
  }), { initialProps: { value: 1 } });
  rerender({ value: 2 });
  await waitFor(() => expect(result.current.error).toBe("Disk is full"));
  rerender({ value: 2 });
  expect(writes).toBe(1);
  act(() => result.current.retry());
  await waitFor(() => expect(persisted).toBe(2));
  expect(result.current.status).toBe("saved");
  unmount();
});

it("reports a failed navigation flush to the remaining application", async () => {
  const reported = vi.fn();
  window.addEventListener(SETTINGS_SAVE_ERROR_EVENT, reported, { once: true });
  const { rerender, unmount } = renderHook(({ value }) => useSettingsAutosave({
    value, savedValue: 1,
    save: next => serializeSettingsSave("failed-navigation-test", async () => { throw new Error(`Disk write failed for edit ${next}`); }),
  }), { initialProps: { value: 1 } });
  rerender({ value: 2 });
  unmount();
  await waitForSettingsSaves("failed-navigation-test");
  await waitFor(() => expect(reported).toHaveBeenCalledOnce());
  expect((reported.mock.calls[0][0] as CustomEvent).detail).toEqual({ error: "Disk write failed for edit 2" });
  window.removeEventListener(SETTINGS_SAVE_ERROR_EVENT, reported);
});
