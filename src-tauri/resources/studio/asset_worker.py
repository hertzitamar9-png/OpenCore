"""Offline studio worker. Request files come from the validated app job queue.

No implicit model downloads or quantization. Upstream workers for additional
architectures may implement the same --request / --output contract.
"""
import argparse
import json
import os
import sys
import struct
from pathlib import Path


def bounded_integer(settings, name, default, low, high):
    value = settings.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def checkpoint_dtypes(model):
    """Read only safetensors headers; never make a float master copy."""
    result = {}
    for component in model.iterdir():
        if not component.is_dir():
            continue
        dtypes = set()
        for checkpoint in component.glob("*.safetensors"):
            with checkpoint.open("rb") as stream:
                size = struct.unpack("<Q", stream.read(8))[0]
                if size > 64 * 1024 * 1024:
                    raise ValueError("Oversized safetensors header")
                header = json.loads(stream.read(size))
            dtypes.update(value["dtype"] for key, value in header.items() if key != "__metadata__" and value["dtype"].startswith(("F", "BF")))
        if dtypes:
            if len(dtypes) != 1 or not dtypes <= {"F32", "F16", "BF16"}:
                raise ValueError(f"{component.name} has mixed or unsupported precision; connect a worker preserving its exact tensor formats")
            result[component.name] = dtypes.pop()
    if not result:
        raise ValueError("No safetensors precision metadata found; connect this model's upstream worker")
    return result


def progress(output, stage, **values):
    temporary = output / "progress.tmp"
    temporary.write_text(json.dumps({"stage": stage, **values}), encoding="utf-8")
    temporary.replace(output / "progress.json")


def generate(request, output):
    settings = request.get("settings") or {}
    model_id = request["modelId"]
    model = Path(request["modelRoot"]) / "models" / "library" / model_id
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    if not model.is_dir():
        raise RuntimeError("The installed model folder is missing")
    output.mkdir(parents=True, exist_ok=True)
    progress(output, "Loading model")
    if model_id == "triposr":
        source = request.get("sourceDir")
        if not source or not (Path(source) / "tsr" / "system.py").is_file():
            raise RuntimeError("Connect the official TripoSR source folder in Assets Studio")
        image = Path(settings.get("inputPath", ""))
        if not image.is_file():
            raise ValueError("TripoSR requires an input image; attach an image or use an earlier image generation")
        sys.path.insert(0, source)
        import torch
        from PIL import Image
        from tsr.system import TSR
        resolution = bounded_integer(settings, "resolution", 256, 32, 512)
        print("Loading TripoSR", flush=True)
        generator = TSR.from_pretrained(str(model), config_name="config.yaml", weight_name="model.ckpt")
        generator.renderer.set_chunk_size(bounded_integer(settings, "chunkSize", 8192, 256, 32768))
        generator.to("cuda")
        progress(output, "Reconstructing 3D asset")
        pixels = Image.open(image).convert("RGB")
        with torch.inference_mode():
            scene = generator([pixels], device="cuda")
            meshes = generator.extract_mesh(scene, True, resolution=resolution)
        meshes[0].export(output / "asset.glb")
    elif model_id in {"qwen-image-21", "animation-diffusion-2d"}:
        import torch
        import diffusers
        from diffusers import DiffusionPipeline
        if tuple(int(n) for n in diffusers.__version__.split('.')[:2]) < (0, 35):
            raise RuntimeError("This worker requires Diffusers 0.35 or later for component-specific precision")
        # Keep the shipped precision; bounded CPU offload lowers VRAM residency.
        formats = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}
        dtypes = {name: formats[dtype] for name, dtype in checkpoint_dtypes(model).items()}
        generator = DiffusionPipeline.from_pretrained(str(model), torch_dtype=dtypes, local_files_only=True)
        generator.enable_model_cpu_offload()
        seed = bounded_integer(settings, "seed", 831001, 0, 2**32-1)
        args = dict(prompt=request["prompt"], num_inference_steps=bounded_integer(settings,"steps",30,1,100),
                    width=bounded_integer(settings,"width",768,128,2048), height=bounded_integer(settings,"height",768,128,2048),
                    generator=torch.Generator("cpu").manual_seed(seed))
        if settings.get("negativePrompt"):
            args["negative_prompt"] = settings["negativePrompt"]
        progress(output, "Generating image", steps=args["num_inference_steps"])
        result = generator(**args)
        if not getattr(result, "images", None):
            raise RuntimeError("The selected pipeline did not return images")
        for index, image in enumerate(result.images):
            image.save(output / f"image-{index+1}.png")
    else:
        raise RuntimeError(f"{model_id} needs its upstream worker connected in Assets Studio. The generic worker does not support this architecture.")
    progress(output, "Generation complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(json.loads(args.request.read_text(encoding="utf-8")), args.output)
