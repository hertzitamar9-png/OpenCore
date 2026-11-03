import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readProfilePreference } from './App';
import { selectProfile } from './api';

const nativeInvoke = vi.hoisted(() => vi.fn(async () => undefined));
vi.mock('@tauri-apps/api/core', () => ({ invoke: nativeInvoke, convertFileSrc: (path: string) => path }));

beforeEach(() => {
  nativeInvoke.mockClear();
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('device-owned model selection', () => {
  it('keeps a fresh phone idle when its backend seeds stopped', () => {
    vi.stubGlobal('__OPENCORE_PHONE__', true);
    window.localStorage.setItem('opencore.model-profile', 'stopped');
    expect(readProfilePreference()).toBe('stopped');
  });

  it('keeps the native backend-validated custom HF profile on reopening', () => {
    vi.stubGlobal('__OPENCORE_PHONE__', true);
    window.localStorage.setItem('opencore.model-profile', 'abc123-phone-owned-model');
    expect(readProfilePreference()).toBe('abc123-phone-owned-model');
  });

  it('defaults a phone with no preference to idle', () => {
    vi.stubGlobal('__OPENCORE_PHONE__', true);
    window.localStorage.removeItem('opencore.model-profile');
    expect(readProfilePreference()).toBe('stopped');
  });

  it('retains the desktop and paired-PC first-run default', () => {
    vi.stubGlobal('__OPENCORE_PHONE__', false);
    window.localStorage.removeItem('opencore.model-profile');
    expect(readProfilePreference()).toBe('doucode');
    window.localStorage.setItem('opencore.model-profile', 'invalid-profile');
    expect(readProfilePreference()).toBe('doucode');
  });

  it('sends the valid idle Phone selection through native IPC', async () => {
    vi.stubGlobal('__OPENCORE_PHONE__', true);
    vi.stubGlobal('__TAURI_INTERNALS__', {});
    await expect(selectProfile('stopped')).resolves.toBeUndefined();
    expect(nativeInvoke).toHaveBeenCalledWith('select_profile', { profile: 'stopped' });
  });

  it('retains the desktop rejection of starting without a selection', async () => {
    vi.stubGlobal('__OPENCORE_PHONE__', false);
    vi.stubGlobal('__TAURI_INTERNALS__', {});
    await expect(selectProfile('stopped')).rejects.toThrow('Choose a model profile');
    expect(nativeInvoke).not.toHaveBeenCalled();
  });
});
