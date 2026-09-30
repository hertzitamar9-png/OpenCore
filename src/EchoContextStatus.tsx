import { useEffect, useState } from "react";
import { echoWorkingSet, type EchoWorkingSet } from "./api";

export function EchoContextStatus({ conversationId, running, configuredContextTokens, attentionKvLocation, attentionKvType }: { conversationId?: string; running: boolean; configuredContextTokens?: number; attentionKvLocation?: string; attentionKvType?: string }) {
  const [state, setState] = useState<EchoWorkingSet | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setState(null); setError(false);
    if (!conversationId) return;
    let active = true, pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try { const value = await echoWorkingSet(conversationId); if (active) { setState(value); setError(false); } }
      catch { if (active) setError(true); }
      finally { pending = false; }
    };
    void refresh();
    if (!running) return () => { active = false; };
    const timer = setInterval(refresh, 2500);
    return () => { active = false; clearInterval(timer); };
  }, [conversationId, running]);
  const windowTokens = state?.contextMode === "persistent_echo" && state.windowTokens
    ? Math.min(state.windowTokens, state.modelContextTokens ?? state.windowTokens)
    : state?.modelContextTokens ?? state?.windowTokens ?? configuredContextTokens ?? 32768;
  const persistentEcho = state?.contextMode === "persistent_echo";
  const transcriptTokens = persistentEcho && !state?.active
    ? state?.liveTokens
    : state?.promptTokens ?? state?.liveTokens;
  const usedTokens = state?.modelActiveTokens ?? transcriptTokens;
  const usageLabel = state?.modelActiveTokens != null
    ? "in the rolling model window"
    : state?.active ? "active" : persistentEcho ? "retained across turns" : "last request";
  const percent = usedTokens == null ? null : Math.min(100, Math.round(usedTokens / windowTokens * 100));
  const warmCache = state?.warmCache;
  const memory = state?.echoVirtualMemory;
  const warmCacheLabel = warmCache
    ? `ECHO RAM cache · ${(warmCache.residentBytes / 1024 / 1024).toFixed(1)} / ${(warmCache.budgetBytes / 1024 / 1024).toFixed(0)} MiB · ${Math.round(warmCache.hitRate * 100)}% hits`
    : null;
  const activity = error ? "Context telemetry reconnecting…" : !state ? running ? "Loading context telemetry…" : "Runtime stopped" : !state.available ? running ? "Usage begins with the first message" : "No saved usage for this conversation yet" : state.active ? "Generation active" : persistentEcho ? running ? "ECHO working set ready · no active turn" : "ECHO history saved · runtime stopped" : running ? "Runtime ready · latest usage" : "Last prompt snapshot";
  return <div className="echo-context-status" role="status" aria-label="ECHO live session">
    <div className="echo-context-meter">
      <strong>{state?.modelSessionTokens != null ? "Live model context" : persistentEcho ? "ECHO working context" : "Live context"}</strong>
      <span className="echo-context-reading">{state?.modelSessionTokens != null
        ? `${state.modelSessionTokens.toLocaleString()} tokens processed in this session · ${usedTokens?.toLocaleString() ?? 0} / ${windowTokens.toLocaleString()} in rolling window`
        : usedTokens == null ? `${windowTokens.toLocaleString()}-token rolling window · awaiting prompt`
          : `${usedTokens.toLocaleString()} ${usageLabel} / ${windowTokens.toLocaleString()} tokens · ${percent}%`}</span>
      {percent != null ? <progress aria-label="Rolling model window usage" value={usedTokens} max={windowTokens} /> : null}
    </div>
    <div className="echo-context-details">
      <span className={`echo-context-chip ${state?.active ? "is-active" : ""}`}>{activity}</span>
      {state?.modelSessionTokens != null ? <span className="echo-context-chip">Model state is reused between turns; at its limit ECHO rebuilds from the retained transcript</span> : null}
      {persistentEcho ? <span className="echo-context-chip">Exact conversation history stays in the ECHO archive for source retrieval</span> : null}
      {persistentEcho && state?.echoActivePages ? <span className="echo-context-chip" title={`${state.echoLastRetrievalReason || "Retrieved from exact archived source"}${state.echoActiveSourceHashes?.length ? ` · source hashes: ${state.echoActiveSourceHashes.join(", ")}` : ""}${state.echoRetrievalLatencyMs != null ? ` · retrieval ${state.echoRetrievalLatencyMs} ms` : ""}`}>ECHO active recall · {state.echoActivePages} pages · {state.echoRecalledTokens?.toLocaleString() ?? 0} tokens</span> : null}
      {state?.modelSessionActive != null ? <span className="echo-context-chip">Model slot · {state.modelSessionActive ? "processing" : "ready"}</span> : null}
      {state?.autoCompactEnabled === false ? <span className="echo-context-chip">SDK summary compaction disabled</span> : null}
      {state?.autoCompactEnabled === true && typeof state.autoCompactThreshold === "number" && state.autoCompactThreshold > 0 ? <span className="echo-context-chip">SDK auto compact · {state.autoCompactThreshold.toLocaleString()}</span> : null}
      {state ? <span className="echo-context-chip">{state.compactions ?? 0} compactions</span> : null}
      {state?.offloadedMessages ? <span className="echo-context-chip">ECHO holds {state.offloadedMessages} archived messages</span> : null}
      {persistentEcho && !state?.active && state?.promptTokens ? <span className="echo-context-chip">Last request · {state.promptTokens.toLocaleString()} tokens</span> : null}
      {warmCacheLabel ? <span className="echo-context-chip" title={`Approximate Python object byte accounting for ${warmCache?.pages ?? 0} decoded exact pages · ${warmCache?.evictions ?? 0} evictions. Excludes allocator arenas, SQLite cache, and GPU KV.`}>{warmCacheLabel}</span> : null}
      {attentionKvLocation && attentionKvLocation !== "not loaded" ? <span className="echo-context-chip" title="Configured by the active profile launch flags; this is not a per-process allocator measurement.">Attention KV configured · {attentionKvType || "unknown"} · {attentionKvLocation}</span> : null}
      {state?.harness?.name === "claude-agent-sdk" && <span className="echo-context-chip">Claude Agent · {state.harness.status}</span>}
    </div>
    {memory ? <details className="echo-memory-diagnostics">
      <summary>ECHO virtual memory · {memory.active_pages.length} active pages</summary>
      <dl>
        <dt>Physical working set</dt><dd>Recent {memory.recent_tokens.toLocaleString()} · pinned {memory.pinned_tokens.toLocaleString()} · recalled {memory.retrieved_tokens.toLocaleString()} · response reserve {memory.reserve_tokens.toLocaleString()}</dd>
        {memory.virtual_history_tokens != null ? <><dt>Addressable history</dt><dd>{memory.virtual_history_tokens.toLocaleString()} archived token estimate · {((memory.virtual_history_bytes ?? 0) / 1024 / 1024).toFixed(1)} MiB original text</dd></> : null}
        <dt>Materialization</dt><dd>{memory.adapter.materialization_mode} · {memory.adapter.adapter} · direct KV reuse {memory.adapter.supports_direct_kv_reuse ? "validated" : "disabled"}</dd>
        <dt>Refresh</dt><dd>{memory.last_refresh_reason} · {memory.refreshes} refreshes · {memory.page_faults} page faults</dd>
        <dt>Latency</dt><dd>Retrieval {memory.retrieval_ms} ms · preparation {memory.rematerialization_prepare_ms} ms{memory.prefill_ms != null ? ` · backend prefill ${memory.prefill_ms} ms` : " · backend prefill not reported"}</dd>
        <dt>Prepared text cache</dt><dd>{memory.materialization_cache_hits} hits · {memory.materialization_cache_misses} misses · {(memory.materialization_ram_bytes / 1024 / 1024).toFixed(1)} MiB RAM</dd>
        {memory.source_bytes_read != null ? <><dt>Canonical page reads</dt><dd>{memory.source_bytes_read.toLocaleString()} bytes · {memory.source_read_ms ?? 0} ms</dd></> : null}
      </dl>
      <ul>{memory.active_pages.map(page => <li key={page.page_id} title={`Source ${page.source_hash}; ${JSON.stringify(page.signals)}`}>{page.page_id} · {page.tier} · score {page.score}</li>)}</ul>
      {memory.diagnostics.length ? <p role="alert">{memory.diagnostics.join("; ")}</p> : null}
    </details> : null}
  </div>;
}
