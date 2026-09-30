import { useEffect, useState } from "react";
import { getEchoMemoryConfiguration, saveEchoMemoryConfiguration, type EchoMemoryConfiguration } from "./api";

export function EchoMemorySettings() {
  const [config, setConfig] = useState<EchoMemoryConfiguration>({ memoryTokens: 4096, refreshTokens: 128, warmCacheMib: 128, activeWindowTokens: 32768 });
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    getEchoMemoryConfiguration().then(value => { if (active) setConfig(value); })
      .catch(error => { if (active) setNotice(String(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const valid = Number.isInteger(config.memoryTokens) && config.memoryTokens >= 0 && config.memoryTokens <= 65536
    && Number.isInteger(config.refreshTokens) && config.refreshTokens >= 64 && config.refreshTokens <= 4096
    && Number.isInteger(config.warmCacheMib) && config.warmCacheMib >= 0 && config.warmCacheMib <= 512
    && Number.isInteger(config.activeWindowTokens) && config.activeWindowTokens >= 4096 && config.activeWindowTokens <= 1000000;
  const save = async () => {
    setSaving(true);
    try {
      const result = await saveEchoMemoryConfiguration(config);
      setConfig(result.configuration);
      setNotice(result.applied ? "Saved. Applies at the next memory refresh boundary." : "Saved for the next ECHO runtime start.");
    } catch (error) { setNotice(`Could not save ECHO settings: ${String(error)}`); }
    finally { setSaving(false); }
  };
  return <div className="echo-memory-settings">
    <label className="appearance-label" htmlFor="echo-active-window">Physical ECHO working window (tokens)</label>
    <input id="echo-active-window" className="appearance-number" type="number" min="4096" max="1000000" step="1" disabled={loading || saving} value={config.activeWindowTokens} onChange={e => setConfig({ ...config, activeWindowTokens: Number(e.target.value) })} />
    <p className="appearance-note">Default 32,768. Includes pinned instructions, recent conversation, recalled pages and response reserve; capped by the actual backend capacity. This does not limit the archive or change the backend's allocated KV format.</p>
    <label className="appearance-label" htmlFor="echo-memory-tokens">Maximum active ECHO recall (tokens)</label>
    <input id="echo-memory-tokens" className="appearance-number" type="number" min="0" max="65536" step="1" disabled={loading || saving} value={config.memoryTokens} onChange={e => setConfig({ ...config, memoryTokens: Number(e.target.value) })} />
    <p className="appearance-note">The controller adapts this allowance to the model window, current conversation and response reserve. Zero disables automatic recall.</p>
    <label className="appearance-label" htmlFor="echo-refresh-tokens">Memory refresh interval (generated tokens)</label>
    <input id="echo-refresh-tokens" className="appearance-number" type="number" min="64" max="4096" step="1" disabled={loading || saving} value={config.refreshTokens} onChange={e => setConfig({ ...config, refreshTokens: Number(e.target.value) })} />
    <p className="appearance-note">Refresh runs at the next generation block boundary after this interval, and on new turns, tool results and memory requests. A backend cannot safely change attention during an active decoding call.</p>
    <label className="appearance-label" htmlFor="echo-warm-mib">ECHO RAM page cache (MiB)</label>
    <input id="echo-warm-mib" className="appearance-number" type="number" min="0" max="512" step="1" disabled={loading || saving} value={config.warmCacheMib} onChange={e => setConfig({ ...config, warmCacheMib: Number(e.target.value) })} />
    <p className="appearance-note">Original pages stay on disk. Active recalled text reaches native model attention through fresh prefill; archived KV is reused only by a validated adapter.</p>
    <button className="wide" onClick={() => void save()} disabled={loading || saving || !valid}>{saving ? "Saving ECHO settings…" : "Save ECHO settings"}</button>
    {notice ? <p className="appearance-note" role="status">{notice}</p> : null}
  </div>;
}
