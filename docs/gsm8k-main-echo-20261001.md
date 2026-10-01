# Main ECHO GSM8K result — 2026-10-01

The complete `openai/gsm8k` main/test run scored **1,211 / 1,319 (91.81198%)**.
All 1,319 saved responses were independently rechecked against the pinned dataset:
question hashes, extracted answers, correctness flags, unique indices and final
score agree. No responses were discarded or regenerated to improve this score.

## Protocol

- Model: `itapitarules/OpenCore-code-10kexperts-3tcontext-apex-echo-max`.
- Model revision: `69cf90d90cf450df2a0a373d69d4146bbacaa84b`.
- GGUF SHA-256: `261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae`.
- Dataset revision: `740312add88f781978c0658806c59bc2815b9866`.
- Eight few-shot examples: the first eight train rows.
- Temperature 0, seed 42, at most 1,024 output tokens per question.
- Independent prompts, `cache_prompt=false`; no tools or archive recall.
- Exact numerical comparison after the required `####` final-answer marker.
- Original shipped APEX weights; Q4_0 CPU KV; physical context capacity 16,384.
- Native backend SHA-256: `3c830680aa79d1163415d3386b45341c8f84195a87d1b66f48f64ef08f5a73a1`.

## Performance and limits

Median native decoding across the captured questions was **26.602 tokens/s** on
the local RTX 4070. Summed question wall time was **21,121.831 seconds** (about
5 hours 52 minutes). Thirty-nine responses reached the output token cap and were
scored as captured; the budget was unchanged throughout the run.

This is an arithmetic benchmark with the protocol above. It does not measure
coding-agent quality or ECHO long-history recall, which was disabled. It was run
on the original checkpoint before the separate UltraData/MiMo training pilot;
GSM8K test data is excluded from that pilot.

Local evidence is kept under
`artifacts/evaluations/gsm8k-echo-20261001`: `protocol.json`, `responses.jsonl`,
`score.json`, `speed-gate.json` and the native server log. Captures and dataset
files remain outside Git. `scripts/evaluation/gsm8k_echo.py` implements the
hash-bound evaluation and capture verification.
