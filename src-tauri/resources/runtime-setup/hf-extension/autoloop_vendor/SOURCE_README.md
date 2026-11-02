# AutoLoop v0.2 — Training-free GGUF layer-search engine

**What it actually does:** Imports a supported, single-file GGUF model; creates new GGUF files by **duplicating existing complete Transformer blocks**, optionally **damping both repeated blocks' terminal residual projections** by scaling their existing floating-point values or quantization scales; starts local `llama-server` for the original and candidates; scores answers to the same objective questions; selects the best variant using *development* results; confirms it once on held-out questions; and **exports the changed GGUF only if confirmation passes**. It never trains, fine-tunes, backpropagates, or modifies the original file.

**What it does not do:** It does not guarantee improvement on every question or every model, and it is **not** a true weight-sharing loop runtime. Damped projection editing is only an approximation to an inference-time residual-substep loop. The exported GGUF contains physical copies of repeated layers, so its file size and inferred parameter count are larger even though no new *unique learned values* are introduced. It cannot add a useful MTP head without trained weights. Automatic accuracy grading is **not** human judgment. No claim of measured quality improvement can be made without testing the actual model.

## Windows quick start: only select a GGUF

1. Extract the supplied `AutoLoop_v0.2.zip` to a local folder, replacing your previous AutoLoop code if you want to upgrade. Install Python 3.10+ (with Tkinter).
2. Double-click **`Start_AutoLoop_Windows.bat`**.
3. The GUI **automatically scans local fixed/removable drives** for `.gguf` files in the background. Click **Found models** to search the detected models by filename or path, double-click to choose one; or click **Browse** to select any GGUF directly. You do not need to wait for a full drive scan before choosing your model.
4. The app locates your existing **`llama-server.exe`** (particularly an adjacent `OpenCore/runtime/llama-server.exe`, nearby llama.cpp builds, or your `PATH`) and chooses an output folder automatically.
5. Press **START OPTIMIZATION**. The original GGUF is not overwritten. Benchmarks are run locally and a changed GGUF is exported only if its improvement passes the evaluation gates. Otherwise the original is retained.

**Advanced ▸** exposes optional overrides for runtime, output path, benchmark, number of variants, GPU layers, context and CPU threads. These are not required for the normal one-click path. You can use **Rescan PC** to look for files added after launching AutoLoop.

**Runtime requirement:** Automatic detection means finding an *already installed* compatible `llama-server.exe` and selecting it for you. The app does not download or install a runtime or GPU driver. If nothing is installed, obtain a matching build from [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), preserving its supporting DLLs, or point Advanced to your existing executable.

**Scanning coverage:** On Windows, the scan prioritizes OpenCore, LM Studio, Hugging Face and common download locations, then walks every locally attached *fixed or removable* drive. It runs in a background thread. Network shares, junctions/symlinks, protected OS directories and locations that reject permission may not be scanned. Files discovered are merely catalogued; finding a GGUF does not imply the architecture supports safe model surgery. The application makes no Internet requests while scanning or optimizing.

## macOS/Linux

Install Python 3 with Tkinter and build or download llama.cpp. Then run:

```sh
python3 -m autoloop gui
```

## Headless CLI

```sh
python -m autoloop scan   # prints the discovered GGUF paths
python -m autoloop inspect /path/to/model.gguf
python -m autoloop build /path/to/model.gguf /path/to/model-looped.gguf --blocks 12 13 --damped
python -m autoloop optimize /path/to/model.gguf --out /path/to/EMPTY_OUTPUT_FOLDER --server /path/to/llama-server --variants 6 --gpu-layers -1
```

`build` creates an **experimental, unverified** variant; `--damped` adds projection-scale residual damping (requires supported tensor encodings). `optimize` is the baseline and held-out benchmark gate. You can provide a private benchmark:

```sh
python -m autoloop optimize model.gguf --out results --benchmark benchmarks.json
```

### Benchmark file format

Use JSON or JSONL with **20 or more** unique entries like:

```json
[
  {"prompt":"What is 37 * 14?", "answer":"518", "kind":"number"},
  {"prompt":"What is the capital of Peru?", "answer":"Lima", "kind":"exact"}
]
```

For realistic comparison, supply **dozens or hundreds of diverse questions resembling actual usage**; math, code, reading, tool use and visual input should be evaluated by appropriate task-specific tests. The bundled 48-question test is just a starting demonstration set. The program uses objective exact/number match on model answers, not human evaluation or an LLM-as-judge. It cannot judge open-ended writing quality. It may reject a genuinely better model if its improvements are outside the tested task set.

### Technical details and compatibility

- Pure Python standard-library app; uses local HTTP to communicate with your own `llama-server` process. No Internet calls in the app.
- GGUF v3, little endian; single-file only; compatible standard `blk.N.*` layouts. A conservative allowlist: `llama`, `qwen2`, `qwen3`, `mistral`, `gemma2`, `phi3`, `gemma3`. Allowlisting **does not guarantee** a working converted model. Unrecognized architecture and heterogeneous blocks are explicitly rejected.
- Reads/writes GGUF raw metadata and tensor bytes, including quantized tensors, without quantization conversions. For **damped** variants, edits terminal projection weights (or the quantization scale fields of known GGML tensor types) in both original and repeated block copies; unsupported tensor types are rejected rather than guessed. Updates `architecture.block_count` and remaps per-block metadata arrays. Preserves tokenizer/config metadata and non-block tensors.
- Potentially enormous temporary disk use: at least one full variant is required. The system checks space and copies the tensor payload in chunks.
- Uses locally launched `llama-server` one model at a time, `127.0.0.1`, random port. Default GPU offload is `-ngl -1`; adjust in UI according to installed llama.cpp build.
- Uses deterministic sampling (`temperature=0`, seed 1234) as a comparison control. Development accuracy picks one candidate; a separate holdout split is used just once. Winner must improve development by at least 2 net questions and holdout by at least 1 net question, with at most 1 holdout regression. These thresholds are heuristic, not statistical significance.
- Multimodal model weights, separate vision projection files and video/frame-specific tasks are not yet fully supported in model surgery or objective evaluation. Use caution with multimodal GGUFs; the current release is focused on compatible text-generating Transformer GGUFs.
- Each run needs a **new output folder** (protects prior results). Re-running over an existing `autoloop-report.json` is intentionally refused.

## Files

- `autoloop/gguf.py`: GGUF v3 binary reader, layer mappings and safe streaming writer.
- `autoloop/runner.py`: Local llama.cpp process management and inference.
- `autoloop/discovery.py`: Background scan of local GGUF files, automatic runtime detection and drive discovery.
- `autoloop/benchmarks.py`: Deterministic grading and development/holdout split.
- `autoloop/engine.py`: Automatic search, candidate comparison and export gate.
- `autoloop/ui.py`: Native desktop application, searchable model library, auto-selected runtime/output, and optional advanced settings.
- `tests/`: Synthetic file roundtrip, failure cases, and mock-runner engine tests.

## Safety and limitations

Never distribute modified model weights without checking the original model license. Do not treat the positive benchmark gate as scientific proof of universal improvement. The transformation is heuristic and may worsen out-of-distribution tasks. Its one-click behavior depends on a compatible `llama-server` installation and supported GGUF.
