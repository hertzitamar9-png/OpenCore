# Local UltraData ECHO checkpoint

The ECHO artifact accepts one additional, exact local checkpoint checksum:
`4551c5333bb6287f0222e15a4d1e3a969df04cb7a69833125f5b3aa80239b91a`.
The Hub download remains pinned to the original revision and checksum.
Installing or listing models preserves an already verified compatible local
checkpoint, with its actual checksum and file modification time in the receipt.
Unknown hashes, changed sizes and stale receipts are rejected.

This 24-example UltraData pilot scored **130/164** on the pinned official
HumanEval scorer, compared with **125/164** for the original: 11 gains and six
regressions, paired exact p=0.3323. This is a public single-turn Python benchmark,
not proof of a general coding improvement. MiMo supplied no verified examples.
The six trained composition tensors remain BF16; the frozen Q6_K/F32 backbone
and the other 471 tensors are unchanged.

Activation requires explicit user selection and a verified local checkpoint;
adding this checksum does not activate it or publish its weights. Preserve the
original before switching `OpenCore-Code-Single-File.gguf`, and record the actual
load test and checkpoint identity in a local activation receipt. Weight files,
benchmark captures and training data stay out of the application repository.
