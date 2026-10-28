"""Offline studio worker. Request files come from the validated app job queue.

No implicit model downloads or quantization. Upstream workers for additional
architectures may implement the same --request / --output contract.
"""
import argparse
import json
import os
import sys
import struct
import inspect
from pathlib import Path

DIFFUSERS_IMAGE_MODELS = {
    "qwen-image-21": None,
    "animation-diffusion-2d": None,
    "sana-16": "fp16",
    "hunyuan-dit-v12-distilled": None,
    "moss-image-sft": None,
    "kromoss": None,
    "flux-2-klein-4b": None,
    "flux-2-klein-base-4b": None,
    "z-image-turbo": None,
    "z-image-base": None,
}


def bounded_integer(settings, name, default, low, high):
    value = settings.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def checkpoint_dtypes(model,variant=None):
    """Read only safetensors headers; never make a float master copy."""
    result = {}
    for component in model.iterdir():
        if not component.is_dir():
            continue
        dtypes = set()
        files=list(component.glob('*.safetensors'))
        def tagged(path):
            import re
            match=re.search(r'\.([A-Za-z][A-Za-z0-9_]*)(?:-\d+-of-\d+)?\.safetensors$',path.name)
            return match.group(1).lower() if match else ''
        selected=[file for file in files if tagged(file)==(variant or '')]
        if not selected and variant:selected=[file for file in files if not tagged(file)]
        for checkpoint in selected:
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


def supported_generation_kwargs(pipeline, request, settings, seed):
    """Map common studio controls only to parameters the selected pipeline supports."""
    parameters = inspect.signature(pipeline.__call__).parameters
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    requested = {
        "num_inference_steps": bounded_integer(settings, "steps", 30, 1, 100),
        "width": bounded_integer(settings, "width", 768, 128, 2048),
        "height": bounded_integer(settings, "height", 768, 128, 2048),
        "generator": seed,
    }
    for key in requested:
        if key not in parameters and not accepts_kwargs:
            raise RuntimeError(f"This model runtime does not support the requested {key} setting")
    if settings.get("negativePrompt"):
        if "negative_prompt" not in parameters and not accepts_kwargs:
            raise RuntimeError("This model runtime does not support negative prompts")
        requested["negative_prompt"] = settings["negativePrompt"]
    if "guidanceScale" in settings:
        name = "guidance_scale" if "guidance_scale" in parameters or accepts_kwargs else "true_cfg_scale" if "true_cfg_scale" in parameters else None
        if not name:
            raise RuntimeError("This model runtime does not support guidance scale")
        value = settings["guidanceScale"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 30:
            raise ValueError("guidanceScale must be a number from 0 to 30")
        requested[name] = float(value)
    count = bounded_integer(settings, "numImages", 1, 1, 8)
    if count != 1:
        name = "num_images_per_prompt" if "num_images_per_prompt" in parameters or accepts_kwargs else "num_images" if "num_images" in parameters else None
        if not name:
            raise RuntimeError("This model runtime does not support generating multiple images per job")
        requested[name] = count
    requested["prompt"] = request["prompt"]
    return requested


def validate_output_format(value, allowed, label):
    if not isinstance(value, str) or value.lower() not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return value.lower()


def save_image_output(image, output, index, settings):
    """Export separately from inference size; large outputs never enlarge GPU latents."""
    output_format=validate_output_format(settings.get('outputFormat','png'),{'png','webp','jpeg'},'outputFormat')
    extension='jpg' if output_format=='jpeg' else output_format
    path=output/f'image-{index}.{extension}'
    if 'outputWidth' not in settings and 'outputHeight' not in settings:
        image.save(path,format=output_format.upper())
        return path
    from PIL import Image, PngImagePlugin
    render=image.size
    width=bounded_integer(settings,'outputWidth',render[0],128,8192)
    height=bounded_integer(settings,'outputHeight',render[1],128,8192)
    if width%8 or height%8:raise ValueError('Output dimensions must be multiples of 8')
    resized=None
    options={}
    try:
        if (width,height)!=render:
            action='Upscaling' if width>render[0] or height>render[1] else 'Resizing'
            progress(output,f'{action} image to {width} × {height} with Lanczos',outputWidth=width,outputHeight=height)
            resized=image.resize((width,height),Image.Resampling.LANCZOS)
        if output_format=='png':
            metadata=PngImagePlugin.PngInfo()
            metadata.add_text('OpenCore.RenderSize',f'{render[0]}x{render[1]}')
            metadata.add_text('OpenCore.OutputSize',f'{width}x{height}')
            metadata.add_text('OpenCore.ResizeMethod','Lanczos' if resized is not None else 'None')
            options['pnginfo']=metadata
        (resized if resized is not None else image).save(path,format=output_format.upper(),**options)
    finally:
        if resized is not None:resized.close()
    return path


def load_moss_pipeline(model,torch,diffusers):
    required=['model_index.json','pipeline_moss_image.py','scheduler/scheduler_config.json',
              'text_encoder/config.json','tokenizer/tokenizer.json','tokenizer/tokenizer_config.json',
              'transformer/config.json','vae/config.json']
    missing=[name for name in required if not (model/name).is_file()]
    if missing:raise RuntimeError('incomplete MOSS package: '+', '.join(missing))
    if tuple(int(part) for part in torch.__version__.split('+')[0].split('.')[:2])<(2,9):
        raise RuntimeError('MOSS requires PyTorch 2.9 or later in its managed runtime')
    if tuple(int(part) for part in diffusers.__version__.split('.')[:2])<(0,38):
        raise RuntimeError('MOSS requires Diffusers 0.38 or later')
    pipeline=diffusers.DiffusionPipeline.from_pretrained(str(model),
        custom_pipeline=str(model/'pipeline_moss_image.py'),local_files_only=True,
        trust_remote_code=True,torch_dtype={'default':torch.bfloat16,'vae':torch.float32})
    pipeline.scheduler=type(pipeline.scheduler).from_config(pipeline.scheduler.config)
    # Detach CPU storage from safetensors file mappings before repeated inference.
    # The model's memory preflight must succeed; no disk-backed weight offload.
    for component in pipeline.components.values():
        if hasattr(component,'_apply'):component._apply(lambda tensor:tensor.clone())
    pipeline.enable_sequential_cpu_offload()
    return pipeline


IMAGE_BASE_REPOSITORIES = {
    'qwen-image-21':'Qwen/Qwen-Image-2.1',
    'flux-2-klein-4b':'black-forest-labs/FLUX.2-klein-4B',
    'flux-2-klein-base-4b':'black-forest-labs/FLUX.2-klein-base-4B',
    'z-image-turbo':'Tongyi-MAI/Z-Image-Turbo',
    'z-image-base':'Tongyi-MAI/Z-Image',
    'animation-diffusion-2d':'ModelsLab/3D-Animation-Diffusion',
    'sana-16':'Efficient-Large-Model/Sana_1600M_1024px_diffusers',
    'hunyuan-dit-v12-distilled':'Tencent-Hunyuan/HunyuanDiT-v1.2-Diffusers-Distilled',
    'moss-image-sft':'OpenMOSS-Team/MOSS-Image-SFT',
    'kromoss':'lodestones/Kromoss',
}


def apply_diffusers_image_lora(generator,model_id,root,settings,source_repository=None):
    if not settings.get('loraPath'):return False
    from comfy_worker import managed_image_lora
    base=IMAGE_BASE_REPOSITORIES.get(model_id) or source_repository
    if not base:raise ValueError('The base repository for this image model is not registered')
    lora=managed_image_lora(root,settings,base)
    if not hasattr(generator,'load_lora_weights') or not hasattr(generator,'set_adapters'):
        raise RuntimeError('This Diffusers pipeline does not expose a compatible LoRA loader')
    strength=settings.get('loraStrength',0.8)
    if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not __import__('math').isfinite(strength) or not 0<=strength<=2:
        raise ValueError('LoRA strength must be between 0 and 2')
    generator.load_lora_weights(str(lora.parent),weight_name=lora.name,adapter_name='opencore-image-lora',local_files_only=True)
    generator.set_adapters(['opencore-image-lora'],adapter_weights=[strength])
    if hasattr(generator,'get_active_adapters') and 'opencore-image-lora' not in generator.get_active_adapters():
        raise RuntimeError('The image pipeline did not activate the selected LoRA')
    return True


def generate(request, output):
    settings = request.get("settings") or {}
    model_id = request["modelId"]
    model = Path(request["modelRoot"]) / "models" / "library" / model_id
    if request.get('modelDirectory'):
        from comfy_worker import canonical_path
        model=canonical_path(request['modelDirectory'])
        hub=canonical_path(Path(request['modelRoot'])/'models/hub')
        if not model.is_relative_to(hub) or model==hub:raise ValueError('Imported image model leaves the managed Hub folder')
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    if not model.is_dir():
        raise RuntimeError("The installed model folder is missing")
    output.mkdir(parents=True, exist_ok=True)
    progress(output, "Loading model")
    from comfy_worker import QWEN_VARIANTS
    if model_id in ('moss-image-sft-int8-convrot','moss-image-sft-int4-convrot') or model_id in QWEN_VARIANTS:
        from comfy_worker import generate_comfy
        generate_comfy(request,output)
    elif model_id == "triposr":
        source = request.get("sourceDir")
        if not source or not (Path(source) / "tsr" / "system.py").is_file():
            raise RuntimeError("Connect the official TripoSR source folder in Game Dev Studio")
        image = Path(settings.get("inputPath", ""))
        if not image.is_file():
            raise ValueError("TripoSR requires an input image; attach an image or use an earlier image generation")
        sys.path.insert(0, source)
        import torch
        from PIL import Image
        from tsr.system import TSR
        resolution = bounded_integer(settings, "resolution", 256, 32, 512)
        seed = bounded_integer(settings, "seed", 831001, 0, 2**32-1)
        output_format = validate_output_format(settings.get("outputFormat", "glb"), {"glb", "obj", "ply"}, "outputFormat")
        print("Loading TripoSR", flush=True)
        torch.manual_seed(seed)
        generator = TSR.from_pretrained(str(model), config_name="config.yaml", weight_name="model.ckpt")
        generator.renderer.set_chunk_size(bounded_integer(settings, "chunkSize", 8192, 256, 32768))
        generator.to("cuda")
        progress(output, "Reconstructing 3D asset")
        pixels = Image.open(image).convert("RGB")
        with torch.inference_mode():
            scene = generator([pixels], device="cuda")
            meshes = generator.extract_mesh(scene, True, resolution=resolution)
        meshes[0].export(output / f"asset.{output_format}")
    elif model_id in DIFFUSERS_IMAGE_MODELS or (model/'model_index.json').is_file():
        import torch
        import diffusers
        from diffusers import DiffusionPipeline
        if tuple(int(n) for n in diffusers.__version__.split('.')[:2]) < (0, 35):
            raise RuntimeError("This worker requires Diffusers 0.35 or later for component-specific precision")
        if model_id in ('moss-image-sft','kromoss'):
            for dimension in ('width','height'):
                if bounded_integer(settings,dimension,1024,128,2048)%16:
                    raise ValueError('MOSS image dimensions must be multiples of 16')
            settings={'steps':50,'width':1024,'height':1024,'guidanceScale':3.0,**settings}
            generator=load_moss_pipeline(model,torch,diffusers)
        else:
            # Keep the shipped precision; bounded CPU offload lowers VRAM residency.
            formats = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}
            variant = DIFFUSERS_IMAGE_MODELS.get(model_id) or request.get('pipelineVariant')
            if variant and (not isinstance(variant,str) or not variant.replace('_','').isalnum() or len(variant)>40):raise ValueError('Invalid pipeline precision variant')
            dtypes = {name: formats[dtype] for name, dtype in checkpoint_dtypes(model,variant).items()}
            load_options = {"torch_dtype": dtypes, "local_files_only": True,'trust_remote_code':False,'use_safetensors':True}
            if variant:load_options["variant"] = variant
            index=json.loads((model/'model_index.json').read_text(encoding='utf-8'))
            name=index.get('_class_name')
            if not isinstance(name,str) or not name.endswith('Pipeline') or not isinstance(getattr(diffusers,name,None),type):
                raise RuntimeError('The installed Diffusers runtime does not support this pipeline class: '+str(name))
            import psutil
            required=sum(file.stat().st_size for part in model.iterdir() if part.is_dir() for file in part.glob('*.safetensors'))+4_000_000_000
            if required>psutil.virtual_memory().available:
                raise RuntimeError(f'This published checkpoint needs approximately {required/1e9:.1f} GB available host RAM including load buffers. Select a smaller supported quantization or free memory.')
            generator = DiffusionPipeline.from_pretrained(str(model), **load_options)
            # Release checkpoint mappings after the host-memory admission check.
            for component in generator.components.values():
                if hasattr(component,'_apply'):component._apply(lambda tensor:tensor.clone())
            generator.enable_model_cpu_offload()
        apply_diffusers_image_lora(generator,model_id,Path(request['modelRoot']).resolve(),settings,request.get('sourceRepository'))
        seed = bounded_integer(settings, "seed", 831001, 0, 2**32-1)
        args = supported_generation_kwargs(generator, request, settings, torch.Generator("cpu").manual_seed(seed))
        if model_id in ('moss-image-sft','kromoss'):args['uncond_skip_layers']=[22]
        # The catalog's animation-diffusion-2d is a plain Stable Diffusion
        # checkpoint for animation-style still images, without a motion adapter.
        progress(output, "Generating image", steps=args["num_inference_steps"])
        result = generator(**args)
        if not getattr(result, "images", None):
            raise RuntimeError("The selected pipeline did not return images")
        for index, image in enumerate(result.images):
            save_image_output(image,output,index+1,settings)
    else:
        raise RuntimeError(f"The installed runtime does not support {model_id}'s architecture. Its weights are retained; choose a supported model in Chat or Media Studio.")
    progress(output, "Generation complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(json.loads(args.request.read_text(encoding="utf-8")), args.output)
