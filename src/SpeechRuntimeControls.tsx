import { invoke } from '@tauri-apps/api/core';
import { useEffect, useRef, useState } from 'react';
import './SpeechRuntimeControls.css';

export interface SpeechRuntimeState {
  modelId: string; enabled: boolean; installed: boolean; idleMode: 'cold' | 'ram'; workerReady: boolean; phase: string;
  runtimePrecision?: 'bf16' | 'fp32'; loadingElapsedMs?: number | null;
  runtimeCacheBytes?: number; runtimeCacheEntries?: {precision: string; bytes: number}[];
  denseCacheHit?: boolean | null; prewarmedForSession?: boolean;
}
export function SpeechRuntimeControls({speech, onRefresh, onNotice}: {
  speech: SpeechRuntimeState; onRefresh?: () => void | Promise<void>; onNotice?: (message: string) => void;
}) {
  const [status, setStatus] = useState(speech);
  const [preparing, setPreparing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  useEffect(() => setStatus(speech), [speech]);
  useEffect(() => {
    if (!preparing) return;
    let alive = true;
    const timer = setInterval(() => {
      void invoke<SpeechRuntimeState>('speech_status').then(value => {if (alive) setStatus(value);}).catch(() => {});
    }, 1000);
    return () => {alive = false; clearInterval(timer);};
  }, [preparing]);
  async function prepare() {
    const id = ++request.current;
    setPreparing(true); setError('');
    try {
      const value = await invoke<SpeechRuntimeState>('speech_prewarm_session');
      if (id !== request.current) return;
      setStatus(value); await onRefresh?.();
    } catch (cause) {if (id === request.current) setError(String(cause));}
    finally {if (id === request.current) setPreparing(false);}
  }
  async function cancel() {
    ++request.current;
    try {setStatus(await invoke<SpeechRuntimeState>('speech_cancel_prewarm')); await onRefresh?.();}
    catch (cause) {setError(String(cause));}
    finally {setPreparing(false);}
  }
  async function clear() {
    setClearing(true); setError('');
    try {
      setStatus(await invoke<SpeechRuntimeState>('speech_clear_runtime_cache')); await onRefresh?.();
      onNotice?.('Cleared the derived dense runtime cache. The installed checkpoint and selected precision are preserved.');
    } catch (cause) {setError(String(cause));}
    finally {setClearing(false);}
  }
  if (speech.modelId !== 'phonon-2') return null;
  const bytes = status.runtimeCacheBytes || 0;
  const active = ['recording', 'transcribing', 'activating-device'].includes(status.phase);
  return <section className="speech-runtime-controls" aria-label="Phonon runtime preparation">
    <strong>Derived dense runtime cache · {(bytes / 1e9).toFixed(2)} GB on disk</strong>
    <p>Prepared from the same installed checkpoint at the selected BF16 or FP32 runtime precision. A verified cache avoids expanding the container on each cold start.</p>
    {status.runtimeCacheEntries?.length ? <small>{status.runtimeCacheEntries.map(entry => `${entry.precision.toUpperCase()}: ${(entry.bytes / 1e9).toFixed(2)} GB`).join(' · ')}{status.denseCacheHit === true ? ' · Last startup used a verified cache' : ''}</small> : <small>The first startup prepares this optional cache when disk space is available.</small>}
    {status.prewarmedForSession ? <p role="status">Prepared in CPU RAM for the next dictation. Your saved cold startup preference is unchanged; the worker exits after dictation or app restart.</p> : status.idleMode === 'cold' ? <p>Prepare the next dictation now to wait for startup before you need the microphone. You can cancel preparation.</p> : null}
    <div className="speech-runtime-actions">
      {status.idleMode === 'cold' && !status.prewarmedForSession && !preparing ? <button disabled={!status.enabled || !status.installed || active || clearing} onClick={() => void prepare()}>Prepare next dictation</button> : null}
      {preparing ? <><span role="status">{status.phase.replaceAll('-', ' ')}{status.loadingElapsedMs != null ? ` · ${(status.loadingElapsedMs / 1000).toFixed(1)} s` : ''}</span><button onClick={() => void cancel()}>Cancel preparation</button></> : null}
      {status.prewarmedForSession && !preparing ? <button onClick={() => void cancel()}>Release prepared RAM</button> : null}
      <button disabled={!bytes || preparing || clearing || active} onClick={() => void clear()}>{clearing ? 'Clearing…' : 'Clear derived cache'}</button>
    </div>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
