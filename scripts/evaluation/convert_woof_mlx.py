"""Convert the pinned publisher Woof MLX affine checkpoint to an HF GGUF adapter.

This reconstructs the published 4-bit affine values; it cannot recover weights
before quantization.  GGUF export itself is delegated to the unmodified official
llama.cpp converter.  Requires NumPy, and MLX CPU for independent validation.

The affine decode follows the pinned Metal quantized weight decoder: unpack
least-significant nibbles first, evaluate scale*q+bias in FP32, and round once to
BF16. MLX's CPU default BF16 dequantize has a different intermediate rounding;
the independent check therefore uses FP32 auxiliaries then an MLX BF16 cast.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

REPO_ID = "ConwayResearch/Underdog-Woof-4B-1.1"
REVISION = "cf5f8db5409258e73303b78e112051fc443cb02b"
SOURCE_SHA256 = "db21a4aae693db80ec907adc6d635c7bcb0c47622dff2ca0bc741af769a8174e"
SOURCE_BYTES = 2_367_237_149
MLX_VERSION = "0.32.3"
GROUP_SIZE = 64
PREFIX = "language_model."
NORM_SUFFIXES = (
    ".input_layernorm.weight",
    ".post_attention_layernorm.weight",
    "model.norm.weight",
    ".q_norm.weight",
    ".k_norm.weight",
)
NATIVE_DTYPES = {"U32": "<u4", "BF16": "<u2", "F32": "<f4"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bf16_to_f32(bits: np.ndarray) -> np.ndarray:
    return (np.asarray(bits, dtype=np.uint16).astype(np.uint32) << 16).view(np.float32)


def f32_to_bf16(values: np.ndarray) -> np.ndarray:
    """IEEE round-to-nearest, ties-to-even; no recovered source precision."""
    values = np.asarray(values, dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite reconstructed affine value")
    words = values.view(np.uint32)
    rounded = words + np.uint32(0x7FFF) + ((words >> 16) & np.uint32(1))
    return (rounded >> 16).astype("<u2")


def unpack_affine4(packed: np.ndarray) -> np.ndarray:
    packed = np.asarray(packed, dtype=np.uint32)
    shifts = np.arange(0, 32, 4, dtype=np.uint32)
    return ((packed[..., None] >> shifts) & np.uint32(15)).reshape(
        *packed.shape[:-1], packed.shape[-1] * 8
    )


def dequantize_affine4(
    packed: np.ndarray, scale_bits: np.ndarray, bias_bits: np.ndarray
) -> np.ndarray:
    if packed.ndim != 2 or scale_bits.shape != bias_bits.shape:
        raise ValueError("Expected a 2D packed matrix with matched affine auxiliaries")
    if (packed.shape[0], packed.shape[1] * 8 // GROUP_SIZE) != scale_bits.shape:
        raise ValueError("Packed/auxiliary shape does not agree with group_size=64")
    codes = unpack_affine4(packed).astype(np.float32)
    # Broadcast groups without allocating a repeated scale/bias matrix.
    grouped = codes.reshape(packed.shape[0], -1, GROUP_SIZE)
    grouped *= bf16_to_f32(scale_bits)[..., None]
    grouped += bf16_to_f32(bias_bits)[..., None]
    return f32_to_bf16(grouped.reshape(codes.shape))


class SafeTensorReader:
    def __init__(self, path: Path):
        self.path = path
        with path.open("rb") as handle:
            self.header_size = struct.unpack("<Q", handle.read(8))[0]
            if self.header_size > 10_000_000:
                raise ValueError("Unexpectedly large safetensors header")
            self.header = json.loads(handle.read(self.header_size))
        self.data_start = 8 + self.header_size
        self.tensors = {k: v for k, v in self.header.items() if k != "__metadata__"}
        self.metadata = self.header.get("__metadata__", {})
        intervals = []
        for name, info in self.tensors.items():
            dtype = info["dtype"]
            if dtype not in NATIVE_DTYPES:
                raise ValueError(f"Unsupported source dtype {name}: {dtype}")
            first, last = info["data_offsets"]
            expected = math.prod(info["shape"]) * np.dtype(NATIVE_DTYPES[dtype]).itemsize
            if first < 0 or last - first != expected:
                raise ValueError(f"Invalid source tensor extent: {name}")
            intervals.append((first, last, name))
        intervals.sort()
        end = 0
        for first, last, name in intervals:
            if first != end:
                raise ValueError(f"Gap/overlap in safetensors payload: {name}")
            end = last
        if self.data_start + end != path.stat().st_size:
            raise ValueError("Safetensors payload does not cover file exactly")

    def read(self, name: str) -> np.ndarray:
        info = self.tensors[name]
        return np.memmap(
            self.path,
            mode="r",
            offset=self.data_start + info["data_offsets"][0],
            dtype=NATIVE_DTYPES[info["dtype"]],
            shape=tuple(info["shape"]),
        )


def expected_shapes(config: dict) -> dict[str, tuple[int, ...]]:
    """Fail closed on source names/layout outside this pinned dense text model."""
    c = config["text_config"]
    hidden = c["hidden_size"]
    middle = c["intermediate_size"]
    key_dim = c["linear_key_head_dim"] * c["linear_num_key_heads"]
    value_dim = c["linear_value_head_dim"] * c["linear_num_value_heads"]
    shapes = {
        "language_model.model.embed_tokens.weight": (c["vocab_size"], hidden),
        "language_model.model.norm.weight": (hidden,),
    }
    for layer, layer_type in enumerate(c["layer_types"]):
        base = f"language_model.model.layers.{layer}"
        shapes.update({
            f"{base}.input_layernorm.weight": (hidden,),
            f"{base}.post_attention_layernorm.weight": (hidden,),
            f"{base}.mlp.gate_proj.weight": (middle, hidden),
            f"{base}.mlp.up_proj.weight": (middle, hidden),
            f"{base}.mlp.down_proj.weight": (hidden, middle),
        })
        if layer_type == "linear_attention":
            attn = f"{base}.linear_attn"
            shapes.update({
                f"{attn}.in_proj_qkv.weight": (2 * key_dim + value_dim, hidden),
                f"{attn}.in_proj_z.weight": (value_dim, hidden),
                f"{attn}.in_proj_a.weight": (c["linear_num_value_heads"], hidden),
                f"{attn}.in_proj_b.weight": (c["linear_num_value_heads"], hidden),
                f"{attn}.out_proj.weight": (hidden, value_dim),
                f"{attn}.A_log": (c["linear_num_value_heads"],),
                f"{attn}.dt_bias": (c["linear_num_value_heads"],),
                f"{attn}.norm.weight": (c["linear_value_head_dim"],),
                f"{attn}.conv1d.weight": (2 * key_dim + value_dim, c["linear_conv_kernel_dim"], 1),
            })
        elif layer_type == "full_attention":
            attn = f"{base}.self_attn"
            shapes.update({
                f"{attn}.q_proj.weight": (2 * c["num_attention_heads"] * c["head_dim"], hidden),
                f"{attn}.k_proj.weight": (c["num_key_value_heads"] * c["head_dim"], hidden),
                f"{attn}.v_proj.weight": (c["num_key_value_heads"] * c["head_dim"], hidden),
                f"{attn}.o_proj.weight": (hidden, c["num_attention_heads"] * c["head_dim"]),
                f"{attn}.q_norm.weight": (c["head_dim"],),
                f"{attn}.k_norm.weight": (c["head_dim"],),
            })
        else:
            raise ValueError(f"Unsupported layer type: {layer_type}")
    return shapes


def verify_source(source: Path) -> tuple[SafeTensorReader, dict, dict]:
    path = source / "model.safetensors"
    if path.stat().st_size != SOURCE_BYTES or sha256_file(path) != SOURCE_SHA256:
        raise ValueError("Checkpoint differs from the pinned publisher source")
    config = json.loads((source / "config.json").read_text())
    quant = {"group_size": GROUP_SIZE, "bits": 4, "mode": "affine"}
    if config.get("quantization") != quant or config.get("quantization_config") != quant:
        raise ValueError("Unsupported source quantization config")
    if config["architectures"] != ["Qwen3_5ForConditionalGeneration"]:
        raise ValueError("Unexpected source architecture")
    c = config["text_config"]
    if c["num_hidden_layers"] != 32 or len(c["layer_types"]) != 32 or not c["tie_word_embeddings"]:
        raise ValueError("Unexpected source layer count or tied embeddings")
    provenance = json.loads((source / "release-provenance.json").read_text())
    if provenance["repo_id"] != REPO_ID:
        raise ValueError("Publisher provenance model mismatch")
    verified_files = {}
    for filename, record in provenance["files"].items():
        file_path = source / filename
        digest = SOURCE_SHA256 if filename == path.name else sha256_file(file_path)
        if file_path.stat().st_size != record["bytes"] or digest != record["sha256"]:
            raise ValueError(f"Publisher provenance digest mismatch: {filename}")
        verified_files[filename] = {"bytes": file_path.stat().st_size, "sha256": digest}
    reader = SafeTensorReader(path)
    if reader.metadata != {"format": "mlx"}:
        raise ValueError("Unexpected safetensors metadata")
    expanded = {}
    for name, info in reader.tensors.items():
        if name.endswith((".scales", ".biases")):
            continue
        shape = tuple(info["shape"])
        if info["dtype"] == "U32":
            if not name.endswith(".weight") or len(shape) != 2:
                raise ValueError(f"Unexpected packed tensor: {name}")
            base = name.removesuffix(".weight")
            auxiliary_shape = (shape[0], shape[1] * 8 // GROUP_SIZE)
            for suffix in (".scales", ".biases"):
                aux = reader.tensors.get(base + suffix)
                if aux is None or aux["dtype"] != "BF16" or tuple(aux["shape"]) != auxiliary_shape:
                    raise ValueError(f"Invalid affine auxiliary: {name}{suffix}")
            shape = (shape[0], shape[1] * 8)
        elif info["dtype"] != "BF16":
            raise ValueError(f"Unexpected unquantized source dtype: {name}")
        expanded[name] = shape
    if expanded != expected_shapes(config):
        missing = sorted(set(expected_shapes(config)) - set(expanded))
        extra = sorted(set(expanded) - set(expected_shapes(config)))
        mismatch = {n: s for n, s in expanded.items() if n in expected_shapes(config) and s != expected_shapes(config)[n]}
        raise ValueError(f"Unexpected source layout: missing={missing}, extra={extra}, mismatch={mismatch}")
    expected_auxiliaries = {
        name.removesuffix(".weight") + suffix
        for name, info in reader.tensors.items() if info["dtype"] == "U32"
        for suffix in (".scales", ".biases")
    }
    if expected_auxiliaries != {n for n in reader.tensors if n.endswith((".scales", ".biases"))}:
        raise ValueError("Unpaired source affine auxiliaries")
    return reader, config, verified_files


def mlx_reference(packed: np.ndarray, scale_bits: np.ndarray, bias_bits: np.ndarray) -> np.ndarray:
    import mlx.core as mx

    mx.set_default_device(mx.cpu)
    values = mx.dequantize(
        mx.array(np.ascontiguousarray(packed)),
        mx.array(bf16_to_f32(scale_bits)),
        mx.array(bf16_to_f32(bias_bits)),
        group_size=GROUP_SIZE, bits=4, mode="affine",
    )
    # Reading the MLX final cast as uint16 preserves every BF16 bit.
    return np.array(values.astype(mx.bfloat16).view(mx.uint16))


def run_fixtures() -> dict:
    import mlx.core as mx

    if importlib.metadata.version("mlx") != MLX_VERSION:
        raise ValueError(f"Independent validation requires pinned MLX {MLX_VERSION}")
    mx.set_default_device(mx.cpu)
    words = np.array([[0x76543210, 0xFEDCBA98] * 8, [0x01234567, 0x89ABCDEF] * 8], dtype="<u4")
    scales = f32_to_bf16(np.array([[0.014312744140625, -0.03125], [0.125, 0.0]], dtype=np.float32))
    biases = f32_to_bf16(np.array([[-0.08740234375, 0.25], [-0.625, -0.25]], dtype=np.float32))
    got = dequantize_affine4(words, scales, biases)
    # Independent scalar unpack based on little-endian bytes, not shifts in the
    # production vectorized U32 path. Covers low/high nibble and group boundary.
    scalar = []
    s32, b32 = bf16_to_f32(scales), bf16_to_f32(biases)
    for row in range(words.shape[0]):
        decoded = []
        for byte in words[row].tobytes():
            decoded.extend((byte % 16, byte // 16))
        scalar.append([np.float32(q) * s32[row, i // 64] + b32[row, i // 64] for i, q in enumerate(decoded)])
    scalar = f32_to_bf16(np.array(scalar, dtype=np.float32))
    np.testing.assert_array_equal(got, scalar)
    np.testing.assert_array_equal(got, mlx_reference(words, scales, biases))
    np.testing.assert_array_equal(unpack_affine4(words)[0], np.tile(np.arange(16, dtype=np.uint32), 8))
    rng = np.random.default_rng(20261006)
    random_words = rng.integers(0, 2**32, size=(7, 32), dtype=np.uint32)
    random_scales = f32_to_bf16(rng.normal(0, 0.05, (7, 4)).astype(np.float32))
    random_biases = f32_to_bf16(rng.normal(0, 0.3, (7, 4)).astype(np.float32))
    np.testing.assert_array_equal(
        dequantize_affine4(random_words, random_scales, random_biases),
        mlx_reference(random_words, random_scales, random_biases),
    )
    tie_words = np.array([0x3F808000, 0x3F818000, 0xBF808000, 0xBF818000, 0, 0x80000000], dtype=np.uint32)
    np.testing.assert_array_equal(f32_to_bf16(tie_words.view(np.float32)), [0x3F80, 0x3F82, 0xBF80, 0xBF82, 0, 0x8000])
    np.testing.assert_array_equal(
        f32_to_bf16(tie_words.view(np.float32)),
        np.array(mx.array(tie_words.view(np.float32)).astype(mx.bfloat16).view(mx.uint16)),
    )
    conv = np.arange(8, dtype=np.uint16).reshape(2, 4, 1)
    hf_conv = np.swapaxes(conv, 1, 2)
    np.testing.assert_array_equal(hf_conv, np.array([[[0, 1, 2, 3]], [[4, 5, 6, 7]]], dtype=np.uint16))
    np.testing.assert_array_equal(np.swapaxes(hf_conv, 1, 2), conv)
    norm = bf16_to_f32(f32_to_bf16(np.array([0.5, 0.99609375, 1.0078125, 1.5], dtype=np.float32)))
    np.testing.assert_array_equal((norm - np.float32(1)) + np.float32(1), norm)
    return {"status": "passed", "mlx_version": mx.__version__, "device": str(mx.default_device()),
            "cases": ["known packed nibbles and group boundaries", "signed affine parameters", "zero scale", "deterministic random packed words", "BF16 ties to even and signed zero", "conv transpose", "FP32 inverse norm shift"]}


def validate_source(reader: SafeTensorReader) -> dict:
    fixtures = run_fixtures()
    samples = []
    norms = 0
    convolutions = 0
    for name, info in reader.tensors.items():
        if info["dtype"] == "U32":
            base = name.removesuffix(".weight")
            rows = sorted({0, info["shape"][0] // 2, info["shape"][0] - 1})
            packed = reader.read(name)[rows]
            scales = reader.read(base + ".scales")[rows]
            biases = reader.read(base + ".biases")[rows]
            reconstructed = dequantize_affine4(packed, scales, biases)
            np.testing.assert_array_equal(reconstructed, mlx_reference(packed, scales, biases))
            samples.append({"name": name, "rows": rows, "columns": reconstructed.shape[1],
                            "sha256_bf16_samples": hashlib.sha256(reconstructed.tobytes()).hexdigest()})
        elif name.endswith(NORM_SUFFIXES):
            norm = bf16_to_f32(reader.read(name))
            np.testing.assert_array_equal((norm - np.float32(1)) + np.float32(1), norm)
            norms += 1
        elif name.endswith(".conv1d.weight"):
            conv = reader.read(name)
            np.testing.assert_array_equal(np.swapaxes(np.swapaxes(conv, 1, 2), 1, 2), conv)
            convolutions += 1
    return {"status": "passed", "fixtures": fixtures, "packed_matrices_checked": len(samples),
            "sampled_elements": sum(len(s["rows"]) * s["columns"] for s in samples),
            "inverse_norms_checked": norms, "inverse_convolutions_checked": convolutions,
            "packed_matrix_samples": samples}


def export_hf(reader: SafeTensorReader, config: dict, source: Path, destination: Path, shard_limit: int) -> dict:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite destination: {destination}")
    destination.mkdir(parents=True)
    tensors = []
    for original_name, info in sorted(reader.tensors.items()):
        if original_name.endswith((".scales", ".biases")):
            continue
        if not original_name.startswith(PREFIX):
            raise ValueError(f"Unexpected MLX model prefix: {original_name}")
        shape = list(info["shape"])
        dtype = "BF16"
        transform = "copy"
        if info["dtype"] == "U32":
            shape[-1] *= 8
            transform = "dequantize_affine4_fp32_then_bf16_rne"
        elif original_name.endswith(NORM_SUFFIXES):
            dtype = "F32"
            transform = "inverse_mlx_norm_plus_one_in_fp32"
        elif original_name.endswith(".conv1d.weight"):
            shape[1], shape[2] = shape[2], shape[1]
            transform = "inverse_mlx_conv_axes_1_2"
        tensors.append({"source_name": original_name, "name": original_name.removeprefix(PREFIX),
                        "shape": shape, "dtype": dtype, "transform": transform,
                        "bytes": math.prod(shape) * (4 if dtype == "F32" else 2)})
    shards = []
    for info in tensors:
        if not shards or (shards[-1] and sum(t["bytes"] for t in shards[-1]) + info["bytes"] > shard_limit):
            shards.append([])
        shards[-1].append(info)
    weight_map = {}
    shard_records = []
    for index, shard in enumerate(shards, 1):
        filename = f"model-{index:05d}-of-{len(shards):05d}.safetensors"
        header = {"__metadata__": {"format": "pt"}}
        offset = 0
        for info in shard:
            header[info["name"]] = {"shape": info["shape"], "dtype": info["dtype"],
                                    "data_offsets": [offset, offset + info["bytes"]]}
            offset += info["bytes"]
            weight_map[info["name"]] = filename
        header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
        header_bytes += b" " * (-len(header_bytes) % 8)
        path = destination / filename
        digest = hashlib.sha256()
        with path.open("xb") as handle:
            first = struct.pack("<Q", len(header_bytes)) + header_bytes
            handle.write(first)
            digest.update(first)
            for info in shard:
                name = info["source_name"]
                values = reader.read(name)
                tensor_digest = hashlib.sha256()
                written = 0
                if info["transform"].startswith("dequantize"):
                    base = name.removesuffix(".weight")
                    scales = reader.read(base + ".scales")
                    biases = reader.read(base + ".biases")
                    # Bound decoding to 16 MiB of reconstructed output per chunk.
                    rows_per_chunk = max(1, (16 * 1024 * 1024) // (info["shape"][-1] * 2))
                    chunks = (dequantize_affine4(values[r:r + rows_per_chunk], scales[r:r + rows_per_chunk], biases[r:r + rows_per_chunk])
                              for r in range(0, values.shape[0], rows_per_chunk))
                elif info["transform"].startswith("inverse_mlx_norm"):
                    chunks = [(bf16_to_f32(values) - np.float32(1)).astype("<f4")]
                elif info["transform"].startswith("inverse_mlx_conv"):
                    chunks = [np.ascontiguousarray(np.swapaxes(values, 1, 2))]
                else:
                    chunks = [values]
                for chunk in chunks:
                    payload = np.ascontiguousarray(chunk).tobytes()
                    handle.write(payload)
                    digest.update(payload)
                    tensor_digest.update(payload)
                    written += len(payload)
                if written != info["bytes"]:
                    raise ValueError(f"Exported extent mismatch: {name}")
                info["payload_sha256"] = tensor_digest.hexdigest()
        shard_records.append({"file": filename, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
        print(f"exported {filename}: {path.stat().st_size:,} bytes", flush=True)
    adapted_config = json.loads(json.dumps(config))
    adapted_config.pop("quantization", None)
    adapted_config.pop("quantization_config", None)
    # Keep the publisher config, including its MTP declaration. Export through
    # llama.cpp with --no-mtp because the actual source contains zero MTP tensors.
    (destination / "config.json").write_text(json.dumps(adapted_config, indent=2) + "\n")
    for filename in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "generation_config.json", "LICENSE"):
        shutil.copyfile(source / filename, destination / filename)
    total_size = sum(info["bytes"] for info in tensors)
    (destination / "model.safetensors.index.json").write_text(json.dumps({"metadata": {"total_size": total_size}, "weight_map": weight_map}, indent=2) + "\n")
    return {"directory": str(destination), "tensor_count": len(tensors), "parameter_count": sum(math.prod(t["shape"]) for t in tensors),
            "payload_bytes": total_size, "shards": shard_records, "tensors": tensors,
            "adapter_purpose": "Input for the official llama.cpp HF converter; not a replacement publisher checkpoint",
            "required_converter_args": ["--outtype", "bf16", "--no-mtp"],
            "omitted_source_tensors": [], "source_mtp_tensors": 0, "source_vision_tensors": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--export-hf", type=Path)
    parser.add_argument("--shard-limit-mib", type=int, default=512)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(f"Refusing to overwrite report: {args.report}")
    reader, config, verified = verify_source(args.source)
    print(f"verified pinned source: {len(reader.tensors)} tensors", flush=True)
    validation = validate_source(reader)
    print(f"independent validation passed: {validation['packed_matrices_checked']} packed matrices; {validation['sampled_elements']:,} sampled elements", flush=True)
    report = {"source": {"repo_id": REPO_ID, "revision": REVISION, "directory": str(args.source), "files": verified,
                         "published_quantization": {"bits": 4, "mode": "affine", "group_size": GROUP_SIZE}},
              "definition": "MLX packed U32 affine codes with BF16 auxiliaries; FP32 scale*q+bias, one BF16 ties-to-even cast",
              "precision_limit": "Reconstructed published quantized values; original pre-quantization weights are unavailable",
              "validation": validation,
              "versions": {n: importlib.metadata.version(n) for n in ("numpy", "mlx", "mlx-cpu")},
              "utility_sha256": sha256_file(Path(__file__))}
    if args.export_hf:
        report["hf_adapter"] = export_hf(reader, config, args.source, args.export_hf, args.shard_limit_mib * 1024 * 1024)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"report: {args.report}", flush=True)


if __name__ == "__main__":
    main()
