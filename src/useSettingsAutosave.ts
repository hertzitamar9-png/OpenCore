import { useCallback, useEffect, useRef, useState } from "react";

export type SettingsSaveStatus = "saved" | "saving" | "error";
export const SETTINGS_SAVE_ERROR_EVENT = "opencore-settings-save-error";
export interface SettingsSaveError { error: string }

// Every writer to a settings resource shares this queue, including writes flushed
// when a settings panel closes. A failed write must not block later edits.
const queues = new Map<string, Promise<unknown>>();
export function serializeSettingsSave<T>(scope: string, operation: () => Promise<T>): Promise<T> {
  const next = (queues.get(scope) ?? Promise.resolve()).catch(() => {}).then(operation);
  queues.set(scope, next);
  void next.finally(() => { if (queues.get(scope) === next) queues.delete(scope); }).catch(() => {});
  return next;
}
export async function waitForSettingsSaves(scope: string): Promise<void> {
  for (;;) {
    const pending = queues.get(scope);
    if (!pending) return;
    await pending.catch(() => {});
    // A newer edit may have joined the queue while this read was waiting.
    if (queues.get(scope) === pending) return;
  }
}

interface AutosaveOptions<T> {
  value: T | null;
  savedValue: T | null;
  enabled?: boolean;
  save: (value: T) => Promise<T>;
  onSaved?: (value: T, submitted: T) => void;
  delayMs?: number;
}

/** Values passed here are already validated. Invalid raw drafts stay in the form. */
export function useSettingsAutosave<T>({ value, savedValue, enabled = true, save, onSaved, delayMs = 450 }: AutosaveOptions<T>) {
  const key = value === null ? null : JSON.stringify(value);
  const savedKey = savedValue === null ? null : JSON.stringify(savedValue);
  const [state, setState] = useState<{ status: SettingsSaveStatus; error: string }>({ status: "saved", error: "" });
  const mounted = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempted = useRef<string | null>(null);
  const failed = useRef<string | null>(null);
  const writes = useRef(0);
  const latest = useRef({ value, key, savedKey, enabled, save, onSaved });
  latest.current = { value, key, savedKey, enabled, save, onSaved };

  const clearTimer = useCallback(() => {
    if (timer.current !== null) { clearTimeout(timer.current); timer.current = null; }
  }, []);
  const flush = useCallback(() => {
    clearTimer();
    const current = latest.current;
    if (!current.enabled || current.value === null || current.key === attempted.current || current.key === failed.current
      || (current.key === current.savedKey && writes.current === 0)) return;
    const submitted = current.value, submittedKey = current.key;
    attempted.current = submittedKey;
    ++writes.current;
    if (mounted.current) setState({ status: "saving", error: "" });
    // Register the write synchronously: a panel mounted by the same navigation
    // must wait for it before loading the previous persisted value.
    let operation: Promise<T>;
    try { operation = current.save(submitted); }
    catch (cause) { operation = Promise.reject(cause); }
    void operation.then(result => {
      if (mounted.current) {
        latest.current.onSaved?.(result, submitted);
        if (latest.current.key === submittedKey) setState({ status: "saved", error: "" });
      }
    }).catch(cause => {
      if (latest.current.key === submittedKey) {
        failed.current = submittedKey;
        const error = cause instanceof Error ? cause.message : String(cause);
        if (mounted.current) setState({ status: "error", error });
        else if (typeof window !== "undefined") {
          // Navigation can close this panel before its final write settles.
          // The application still needs to surface that failure.
          window.dispatchEvent(new CustomEvent<SettingsSaveError>(SETTINGS_SAVE_ERROR_EVENT, { detail: { error } }));
        }
      }
    }).finally(() => { --writes.current; });
  }, [clearTimer]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; flush(); };
  }, [flush]);
  useEffect(() => {
    clearTimer();
    if (!enabled || key === null) return;
    if (failed.current !== key) failed.current = null;
    if (key === failed.current) return;
    if (key === savedKey && writes.current === 0) {
      attempted.current = null;
      setState({ status: "saved", error: "" });
      return;
    }
    if (key !== attempted.current) {
      setState({ status: "saving", error: "" });
      timer.current = setTimeout(flush, delayMs);
    }
    return clearTimer;
  }, [key, savedKey, enabled, delayMs, clearTimer, flush]);

  const retry = useCallback(() => { failed.current = null; attempted.current = null; flush(); }, [flush]);
  return { ...state, retry, flush };
}
