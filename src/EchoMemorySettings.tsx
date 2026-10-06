import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { getEchoMemoryConfiguration, saveEchoMemoryConfiguration, type EchoMemoryConfiguration } from "./api";
import { platformNativeAvailable } from "./agent-platform";
import { serializeSettingsSave, useSettingsAutosave, waitForSettingsSaves } from "./useSettingsAutosave";

const fields = {
  memoryTokens: { label: "Active ECHO recall", min: 0, max: 65536 },
  refreshTokens: { label: "Memory refresh interval", min: 64, max: 4096 },
  warmCacheMib: { label: "ECHO RAM page cache", min: 0, max: 512 },
  activeWindowTokens: { label: "Physical ECHO working window", min: 4096, max: 1000000 },
};
const keys = Object.keys(fields) as (keyof EchoMemoryConfiguration)[];
function validField(key: keyof EchoMemoryConfiguration, text: string): boolean {
  const value = Number(text), bounds = fields[key];
  return text.trim() !== "" && Number.isInteger(value) && value >= bounds.min && value <= bounds.max;
}

export function EchoMemorySettings() {
  const [saved, setSaved] = useState<EchoMemoryConfiguration | null>(null);
  const [draft, setDraft] = useState({ memoryTokens: "4096", refreshTokens: "128", warmCacheMib: "128", activeWindowTokens: "32768" });
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const applied = useRef(false);
  const native = platformNativeAvailable();
  useEffect(() => {
    let active = true;
    setLoading(true); setLoadError("");
    waitForSettingsSaves("echo-memory").then(getEchoMemoryConfiguration).then(value => {
      if (active) {
        setSaved(value);
        setDraft({ memoryTokens: String(value.memoryTokens), refreshTokens: String(value.refreshTokens), warmCacheMib: String(value.warmCacheMib), activeWindowTokens: String(value.activeWindowTokens) });
      }
    }).catch(error => { if (active) setLoadError(String(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [loadAttempt]);
  const errors = keys.filter(key => !validField(key, draft[key]));
  const candidate = saved ? { ...saved } : null;
  if (candidate) for (const key of keys) if (validField(key, draft[key])) candidate[key] = Number(draft[key]);
  const autosave = useSettingsAutosave({
    value: candidate, savedValue: saved, enabled: native && !loading && saved !== null,
    save: configuration => serializeSettingsSave("echo-memory", async () => {
      const result = await saveEchoMemoryConfiguration(configuration);
      applied.current = result.applied;
      return result.configuration;
    }),
    onSaved: (configuration, submitted) => {
      setNotice(applied.current ? "Saved. Applies at the next memory refresh boundary." : "Saved for the next ECHO runtime start.");
      setSaved(configuration);
      setDraft(current => {
        const next = { ...current };
        for (const key of keys) if (current[key] !== "" && Number(current[key]) === submitted[key]) next[key] = String(configuration[key]);
        return next;
      });
    },
  });
  function control(key: keyof EchoMemoryConfiguration) {
    return { disabled: loading || saved === null, value: draft[key], "aria-invalid": !validField(key, draft[key]),
      onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft(current => ({ ...current, [key]: event.target.value })) };
  }
  return <div className="echo-memory-settings">
    <label className="appearance-label" htmlFor="echo-active-window">Physical ECHO working window (tokens)</label>
    <input id="echo-active-window" className="appearance-number" type="number" min="4096" max="1000000" step="1" {...control("activeWindowTokens")} />
    <p className="appearance-note">Default 32,768. Includes pinned instructions, recent conversation, recalled pages and response reserve; capped by the actual backend capacity. This does not limit the archive or change the backend's allocated KV format.</p>
    <label className="appearance-label" htmlFor="echo-memory-tokens">Maximum active ECHO recall (tokens)</label>
    <input id="echo-memory-tokens" className="appearance-number" type="number" min="0" max="65536" step="1" {...control("memoryTokens")} />
    <p className="appearance-note">The controller adapts this allowance to the model window, current conversation and response reserve. Zero disables automatic recall.</p>
    <label className="appearance-label" htmlFor="echo-refresh-tokens">Memory refresh interval (generated tokens)</label>
    <input id="echo-refresh-tokens" className="appearance-number" type="number" min="64" max="4096" step="1" {...control("refreshTokens")} />
    <p className="appearance-note">Refresh runs at the next generation block boundary after this interval, and on new turns, tool results and memory requests. A backend cannot safely change attention during an active decoding call.</p>
    <label className="appearance-label" htmlFor="echo-warm-mib">ECHO RAM page cache (MiB)</label>
    <input id="echo-warm-mib" className="appearance-number" type="number" min="0" max="512" step="1" {...control("warmCacheMib")} />
    <p className="appearance-note">Original pages stay on disk. Active recalled text reaches native model attention through fresh prefill; archived KV is reused only by a validated adapter.</p>
    <p className="appearance-note" role="status" aria-label="ECHO settings save status" aria-live="polite">{loading ? "Loading ECHO settings…" : !native ? "Preview · Changes are not persisted" : autosave.status === "saving" ? "Saving ECHO settings…" : autosave.status === "error" ? "Could not save ECHO settings" : saved === null ? "ECHO settings unavailable" : notice || "Saved · Changes save automatically"}</p>
    {loadError && <><p className="platform-error" role="alert">Could not load ECHO settings: {loadError}</p><button onClick={() => setLoadAttempt(value => value + 1)}>Retry loading ECHO settings</button></>}
    {autosave.error && <><p className="platform-error" role="alert">Could not save ECHO settings: {autosave.error}</p><button onClick={autosave.retry}>Retry saving ECHO settings</button></>}
    {errors.length > 0 && <p className="platform-error" role="alert">{errors.map(key => `${fields[key].label} requires an integer from ${fields[key].min.toLocaleString()} to ${fields[key].max.toLocaleString()}.`).join(" ")} Other valid edits save automatically.</p>}
  </div>;
}
