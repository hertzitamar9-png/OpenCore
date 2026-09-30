# ECHO virtual context — implemented runtime

ECHO is the existing canonical archive, upgraded with automatic active recall.
Historical content becomes part of the next native generation prompt. The
backend tokenizes and prefills that bounded prompt at valid current positions.
The archive is addressable history; it is not simultaneous attention over every
historical token. Its capacity is limited by disk.

## Data flow

1. Preserve original messages, tools, source events and available assets in the
   existing compressed, checksummed SQLite archive. Existing formats remain
   readable. Derived tables are additive.
2. Build a query from the current input, recent bounded conversation and saved
   task symbols. Use lexical, exact entity/symbol, character n-gram, temporal,
   conversation/project, continuity and reuse signals.
3. Enforce the app's project assignment allowlist. Semantic similarity never
   grants access to another project. Neighbor and relationship expansion retain
   each original conversation scope.
4. Rank and select coherent original pages within the explicit attention budget.
   Duplicate evidence is avoided; older and newer conflicting decisions keep
   timestamps and explicit supersession links. Generic historical references
   can use scoped task/recency routing. Greetings require no history lookup.
5. Cache decoded source pages in the bounded RAM LRU. Cache rendered evidence and
   current-tokenizer counts in bounded RAM/SQLite derivative caches, keyed by
   model/checkpoint identity, scope and layout. Unverified remote model aliases
   use a runtime epoch so incompatible token counts cannot survive a restart.
6. Rematerialize through the native backend's normal prefill. Unchanged native
   prefixes can use its prefix cache. A changed processed prefix, checkpoint
   compaction or model identity forces reset and replay. Stored recent token
   counts are rebuilt once for a new model.
7. Refresh on new turns, tool results, safe generation block boundaries and
   explicit missing-memory requests. A fault's query stays relevant through the
   current answer. Do not interrupt an arbitrary decoder call every 128 tokens.

## Controls and diagnostics

Settings contains an exact token trigger for native-model compaction, including
the effective trigger after response/tool headroom is reserved. ECHO settings
control the physical working window (default 32,768 tokens, capped by backend
capacity), maximum recalled tokens, generated-token refresh interval and accounted
RAM page-cache size. Working-window changes take effect on the next turn;
they do not resize the backend's allocated KV or change its precision.
Settings persist across restart and can apply to a running
ECHO service at the next safe boundary.

Live Context separates recent, pinned, recalled and response-reserve tokens,
addressable archive size, active page IDs/hashes/ranking signals, refreshes,
faults, cache counters, source reads and latency. Backend prefill timings appear
only when actually reported. Archive token size is explicitly an estimate;
native slot occupancy is a separate measured value. SDK prompt usage no longer
replaces ECHO telemetry. Normal chat is not filled with memory debug events.

"HOT active prompt" identifies selected evidence in the conversation's working
prompt; it does not claim exported per-page GPU KV residency. Backend KV
placement is reported separately. WARM decoded/prepared pages are bounded RAM
caches; COLD original content remains in SQLite on SSD. At most two likely
neighbors are prefetched asynchronously.

## Correctness and recovery

Original pages remain the source of truth. Prepared caches can be discarded;
corrupt derivative payloads rebuild from canonical source. Cache write failure
falls back to fresh tokenization with an explicit diagnostic. Retrieval failures
retain already verified, authorized evidence if it still fits. Unknown physical
capacity fails with an actionable configuration error instead of inventing a
262K window. Conversation deletion removes derived text and saved working state.

Virtual-history accounting uses transactional per-scope counters and a one-time
additive migration, not a full archive scan per request. Ordinary idle operation
keeps the incremental indexes intact rather than deleting and rebuilding them.

Final review reproduced and fixed: fault evidence overwritten at a refresh
boundary; mismatched recall/generation headroom; checkpoint compaction without
native reset; generic historical questions rejected by the relevance filter;
and an older deferred window setting overriding a newer idle setting.
Focused regression tests cover all five. Latest backend prefill telemetry is
preserved through subsequent refreshes.

Desktop integration also reproduced an empty-telemetry crash when opening legacy
conversations before their first virtual-memory refresh. Uninitialized telemetry
is now null, and the viewer accepts legacy empty/partial objects without crashing.

The installed-app chat check also caught SDK budget/environment updates replacing
the real user request: those updates had a user-shaped envelope for native-template
compatibility. They now retain explicit harness provenance. ECHO excludes them
when selecting/pinning the current request and finding returned tool results.
Literal user text resembling the envelope remains a user request. Regression
tests verify the original question and tool-result continuation survive both.
The footer now uses the configured ECHO working window even when the native
backend exposes a larger capacity, matching the dedicated Live Context viewer.

## Verified real inference — 2026-09-30

`scripts/echo_virtual_inference_smoke.py` used the existing original ECHO
checkpoint, SHA-256
`261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae`,
5,476,762,688 bytes, with its compatible existing native runtime and app profile
flags. Weight precision was not changed. The test used an 8,192-token physical
window, 256-token output budget and a fact that existed only in the cold archive.
Every answer returned the exact key `blue-garnet-742` through normal generation.

| Synthetic distractor words | Native slot tokens | Recalled tokens | Total GPU MiB | ECHO resident RAM MiB | Archive MiB | Reply seconds | Retrieval ms | Prepared text ms | Native prefill ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 713 | 190 | 6,747 | 5.14 | 0.81 | 2.883 | 36.88 | 1.50 | 2356.951 |
| 32,768 | 713 | 190 | 6,747 | 5.14 | 1.35 | 0.854 | 60.50 | 0.23 | 347.597 |
| 100,000 | 759 | 190 | 6,747 | 5.14 | 2.28 | 0.913 | 99.59 | 0.19 | 351.384 |
| 1,000,000 | 806 | 190 | 6,747 | 5.14 | 19.19 | 0.905 | 101.76 | 0.20 | 354.888 |

Generation was 23.8–24.8 tokens/s for these short replies. The prepared evidence
cache had three hits after its initial miss. At the last checkpoint the source
page cache reported 69.8% hits. GPU figures are total device readings, not an
isolated allocator measurement. RAM is Windows resident working set, excluding
OS file cache. Distractor words are synthetic whitespace words, not a claim that
a million model tokens were simultaneously attended to. These four short checks
are functional/performance evidence, not model-quality or coding benchmark scores.

Tests also cover multiple distant facts, project continuity, exact code symbols,
conflicts/causal links, scoped project reuse, corruption, cache bounds, bounded
attention under repeated history growth, cross-model archive reuse, explicit
faults, tool/checkpoint boundaries, image preservation and live streaming.

## Compatibility and limitations

- The actual inference check used the installed original ECHO model/runtime.
  Adapter capability/fallback tests cover Llama, Qwen, Mistral, Gemma, Phi, MoE,
  unknown Transformers and recurrent/hybrid families. They are not inference
  qualification of every checkpoint in those families.
- Generic mode uses fresh text prefill. Recurrent mode resets/replays the bounded
  working set when required. Direct historical KV reuse, RoPE rebasing, exported
  per-page KV and stored KV quantization remain disabled: these backends expose
  no validated portable relocation/export interface. Existing native KV format
  choices are preserved.
- The semantic channel remains the existing character n-gram similarity, with
  no newly downloaded embedding model. Causal/supersession links are explicit;
  production ingestion does not infer every contradiction or causal relation.
- Project lookup fanout is capped at 128 app-authorized conversations. Retrieval
  is heuristic; retaining original bytes does not guarantee perfect recall of
  every possible historical question.
- Prepared-text latency is not KV reconstruction latency. Native prefill timing
  is the actual backend measurement. The configured RAM budget is conservative
  object accounting, not a guarantee on the entire process working set.
- No HumanEval/LiveBench run, model training, ASR cleanup or precision conversion
  was performed. Release activation repaired the existing ECHO installation:
  the original weights were checksum-verified and their receipt refreshed; the
  missing pinned BF16 vision projector was restored. Nanbeige BF16 native/ECHO profiles remain
  available in the app; their checkpoint was not installed for this verification.

This is a working bounded virtual-context inference path, with truthful backend
capabilities. It does not provide physically infinite attention or guarantee a
trillion-token response.
