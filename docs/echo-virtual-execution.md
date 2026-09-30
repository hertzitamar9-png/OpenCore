# ECHO virtual context execution

User specification: universal, automatically active archival memory with bounded physical context, original history preserved, safe model adapters, incremental indexing, residency, refresh, page faults and honest diagnostics.

## Audit

Existing canonical storage: compressed, checksummed SQLite pages and typed source events/assets; FTS/entity indexes; scope checks; shared bounded RAM page cache; durable live transcript; generation continuations; backend prefix caching and an optional recurrent append protocol. These are retained.

Found defects: injected recall was rearchived by the second completed-turn loop; active recall accumulated beyond its per-call allowance; recall only ran at new user turns; token accounting measured a different wrapper than the injected text; backend identity and materialization capabilities were not an explicit boundary.

## Execution ledger

1. Fix archival duplication and exact accounting.
2. Add capability adapters and fingerprinted materialization cache; raw KV reuse disabled because no adapter has demonstrated safe relocation.
3. Add bounded active memory, adaptive multi-signal selection, causal links, structured routing state, scope enforcement, and refresh/page-fault boundaries.
4. Wire into generation and live diagnostics; preserve backend prefix when selected content is unchanged, reset and re-prefill if previously processed memory is replaced.
5. Run functional continuity, conflict, corruption, cross-model and scaling tests, actual local inference smoke, app regression/release tests, independent review, push and activate.

Ruling: backend APIs own fresh tokenization/prefill and native KV or recurrent state. ECHO caches exact page renderings/token costs and relies on backend prefix caching; it never labels rendered text as cached KV. Direct KV and stored KV quantization stay unavailable until a backend exposes a verified cache relocation/export contract.

Ruling: periodic refresh happens at natural generation block boundaries (and every tool result/new turn/page fault). Existing APIs cannot mutate attention concurrently with decoding. Do not truncate reasoning every 128 tokens to simulate a decoder hook.

Ruling: preserve BF16 Nanbeige and other selected model precision; no HumanEval/LiveBench runs or model downloads, no storage cleanup.

Status: subsystems 1–4 complete. Functional tests, real cold-archive inference at
10K/32K/100K/1M synthetic distractor words, frontend/build/Rust/protocol tests and
independent review completed. Review found five P2 failures; fault query retention,
headroom accounting, checkpoint native reset, anaphoric recall and latest-setting
precedence were fixed with
regressions. Native backend remains responsible for fresh KV; direct reuse is
disabled. Final release/activation is the remaining gate.

Ruling: ECHO's physical working set defaults to 32K within the backend's actual
capacity and is configurable separately from native-model compaction. This
bounds long-session prefill work without claiming that the archive or backend KV
allocation is a simultaneously attended infinite window.
