# Underdog Woof 4B 1.1 prepared Windows runtime

The publisher checkpoint is [ConwayResearch/Underdog-Woof-4B-1.1 at cf5f8db5409258e73303b78e112051fc443cb02b](https://huggingface.co/ConwayResearch/Underdog-Woof-4B-1.1/tree/cf5f8db5409258e73303b78e112051fc443cb02b). It contains MLX affine 4-bit weights with group size 64. OpenCore's public **Install** action downloads those original pinned files. Downloading them leaves `sourceDownloaded: true`, `runtimeReady: false`, `selectable: false`, and `preparedReady: false` until an approved converted runtime is registered.

## Use the retained conversion in OpenCore

1. In Models, select the Woof Native or ECHO mode and choose **Install** to download its publisher source. Stop the model runtime before changing model files.
2. Choose **Use prepared GGUF** and select `Underdog-Woof-4B-1.1-MLX4bit-dequant-BF16.gguf`, followed by its adjacent `Underdog-Woof-4B-1.1-MLX4bit-dequant-BF16.manifest.json`.
3. Wait for source verification, GGUF verification, and copying to finish. Existing model installation progress and cancellation also apply to this operation. A wrong hash, oversized manifest, changed input, insufficient disk space, or cancellation leaves preparation incomplete and creates no new readiness receipt.
4. After `preparedReady: true`, choose **Use model**. Install the sibling mode when needed; it reuses the original source files and the same prepared GGUF without downloading or copying them again.

The retained evaluation conversion is in `C:\Users\hertz\Documents\OpenCore Verification\model-eval-20261006\woof-conversion`. Registration preserves those external originals. It copies the approved GGUF into the installation root at `models/prepared/underdog-woof-4b-11/Underdog-Woof-4B-1.1-MLX4bit-dequant-BF16.gguf`, retains the manifest at that path plus `.manifest.json`, and writes a receipt under `models/receipts` only after verification. New copies require 8,424,393,184 free bytes plus the existing 64 MiB installation reserve. An existing approved managed file is verified and reused; an existing unapproved file is preserved and registration fails.

The ordinary **Uninstall** confirmation lists only declared managed source, prepared runtime, manifest, partial download, and receipt files. It retains shared files while another installed mode uses them. The original conversion directory, unrelated files, and model directories are never recursively removed.

## Immutable identities

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| Publisher `model.safetensors` | 2,367,237,149 | `db21a4aae693db80ec907adc6d635c7bcb0c47622dff2ca0bc741af769a8174e` |
| Prepared GGUF | 8,424,393,184 | `965b2ae8d2b570e01f7d8d5da70f26e697c6bc587878eedb89112a37ef980df5` |
| Adjacent conversion manifest | 6,960 | `0d3101797d98295dbf984eff912d368a7bbb4ddb1543a394cf35c0a505808e2a` |

The manifest binds publisher source identity, converter and reference commits, validation counts, and output identity. Its embedded original paths are provenance; registration never executes them or uses them as destinations. The unchanged manifest is retained at `src-tauri/test-fixtures/woof-affine4-bf16.manifest.json` for the native schema regression.

## Conversion utility and required flags

`convert_woof_mlx.py` validates the source digest, configuration, release provenance, tensor layout, affine unpacking, and independent MLX fixtures. It reconstructs an HF adapter using NumPy and its own streaming safetensors reader/writer. Its independent fixtures require prebuilt MLX CPU 0.32.3. The official GGUF converter also requires its Python dependencies, including a prebuilt CPU Torch wheel; installing the final GGUF in OpenCore requires none of these Python packages.

The qualified conversion used a persistent WSL Ubuntu 24.04 Python 3.12 environment at `/root/.venvs/woof-conversion-20261006`. It installed prebuilt MLX CPU 0.32.3, NumPy 2.2.6, Torch 2.11.0+cpu, Transformers 4.57.6, safetensors 0.8.0, SentencePiece 0.2.2, and protobuf 4.25.9. No local native compilation was used. The source directory must include the retained `release-provenance.json` binding the exact publisher files.

Run the adapter with explicit output paths in a dedicated conversion directory:

```sh
python scripts/evaluation/convert_woof_mlx.py \
  --source "$SOURCE" \
  --report "$CONVERSION/validation.json" \
  --export-hf "$CONVERSION/hf-adapter" \
  --shard-limit-mib 512
```

Use the unmodified [official llama.cpp converter at bed0a856606ee4a24a164066f73d2379447033f5](https://github.com/ggml-org/llama.cpp/blob/bed0a856606ee4a24a164066f73d2379447033f5/convert_hf_to_gguf.py), with its matching `gguf-py` package. The qualified arguments are:

```sh
python "$LLAMA_CPP/convert_hf_to_gguf.py" "$CONVERSION/hf-adapter" \
  --outfile "$CONVERSION/Underdog-Woof-4B-1.1-MLX4bit-dequant-BF16.gguf" \
  --outtype bf16 --no-mtp --use-temp-file \
  --model-name "Underdog-Woof-4B-1.1 MLX affine4bit reconstructed"
```

`--no-mtp` follows the verified source: it contains zero MTP tensors even though its configuration advertises MTP layers. All 426 parameter tensors are preserved. Layout handling includes convolution transposes, inverse MLX norm shifts, and Qwen3.5 value-head ordering. The retained reports independently checked all 249 packed matrices, 2,698,752 source values, and all 426 GGUF tensors with 3,660,288 sampled output values.

## Precision and verification limits

This output is **mostly BF16 GGUF** (`file_type: 32`): 249 BF16 matrices plus 177 F32 norm and state tensors. Decoding unpacks least significant nibbles first, evaluates `scale*q+bias` in FP32, and applies one BF16 round to nearest with ties to even, following the pinned MLX Metal decoder. The independent CPU reference uses FP32 auxiliaries before the BF16 cast because default MLX CPU BF16 decoding rounds intermediate arithmetic differently.

BF16 storage reconstructs the published quantized affine values. It does not recover the unavailable weights before quantization or establish inference parity between MLX and llama.cpp. Windows/GPU inference, benchmarks, and Native/ECHO service readiness require their own runtime evidence. No extra GGUF quantization was applied.

Native Rust tests and app builds run only through the Windows GitHub Actions workflow. Source-only readiness, exact retained manifest validation, rejected hashes, changed metadata, cancellation, and shared-mode removal have focused native tests; writing them does not claim that they have already run. Listings follow the existing checkpoint policy: registration hashes the complete runtime, then readiness requires the approved receipt and unchanged size/mtime; the small manifest is rehashed each time.
