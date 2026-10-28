"""Offline tiled Real-ESRGAN upscaling for the managed image runtimes.

The app prepares MODEL_FILENAME as a verified dependency before starting a job.
This module never downloads weights or executes checkpoint code. Call it after
the diffusion pipeline has been unloaded (or the owned Comfy process has exited)
before choosing device="cuda". The default is deliberately CPU.

upscale_image(image, checkpoint_path, target_size=(width, height), device="cuda",
              progress=lambda stage, **details: ...) returns a new PIL image.
The learned 4x restoration model can synthesize plausible detail; it cannot
guarantee that missing detail matches reality. Final arbitrary sizing and alpha
resampling use Lanczos after learned inference.

Upstream: https://github.com/xinntao/Real-ESRGAN (BSD-3-Clause).
"""
import hashlib
import math
from pathlib import Path


MODEL_ID = "realesr-general-x4v3"
MODEL_FILENAME = "realesr-general-x4v3.pth"
MODEL_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
MODEL_SHA256 = "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292"
MODEL_BYTES = 4_885_111
MODEL_LICENSE = "BSD-3-Clause"
MODEL_SCALE = 4
MAX_DIMENSION = 8192
# The compact checkpoint has 34 spatial 3x3 convolutions (radius 34).
# Forty input pixels also cover the final Lanczos downsampling support.
TILE_PAD = 40


def _bounded_integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")
    return value


def load_upscale_model(checkpoint_path, device="cpu"):
    """Admit only the byte-pinned publisher checkpoint, then load safe tensors."""
    if device not in ("cpu", "cuda"):
        raise ValueError("AI upscale device must be cpu or cuda")
    path = Path(checkpoint_path).resolve(strict=True)
    # Hash and deserialize through the same handle; no unsafe pickle fallback.
    with path.open("rb") as stream:
        if path.stat().st_size != MODEL_BYTES or hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
            raise ValueError(f"AI upscale checkpoint SHA-256 mismatch: reinstall {MODEL_FILENAME}")
        stream.seek(0)
        import torch
        from spandrel import ImageModelDescriptor, ModelLoader
        state = torch.load(stream, map_location="cpu", weights_only=True)
    model = ModelLoader().load_from_state_dict(state).eval()
    if not isinstance(model, ImageModelDescriptor) or model.architecture.id != "Compact" or model.scale != MODEL_SCALE or model.input_channels != 3 or model.output_channels != 3:
        raise ValueError("The pinned AI upscaler did not load as a 4x RGB Compact model")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable for AI upscaling")
    return model.to(device=device, dtype=torch.float16 if device == "cuda" else torch.float32)


def _upscale_pass(image, model, target_size, *, tile_size, tile_pad=TILE_PAD, progress=None):
    """Run bounded padded tiles, stitching directly into the requested CPU size.

    Floating source boxes retain global sample alignment when a native 4x result
    is reduced to e.g. 2x. Padding supplies the model context and Lanczos halo;
    hard-cropping each tile before resizing would introduce boundary artifacts.
    No full input or output image tensor is allocated on the GPU.
    """
    import numpy as np
    from PIL import Image
    import torch

    width, height = image.size
    out_width, out_height = target_size
    output = Image.new("RGB", target_size)
    total = math.ceil(width / tile_size) * math.ceil(height / tile_size)
    completed = 0
    try:
        with torch.inference_mode():
            for top in range(0, height, tile_size):
                for left in range(0, width, tile_size):
                    right, bottom = min(width, left + tile_size), min(height, top + tile_size)
                    padded = (max(0, left - tile_pad), max(0, top - tile_pad),
                              min(width, right + tile_pad), min(height, bottom + tile_pad))
                    # Rounding every global boundary identically avoids gaps at
                    # arbitrary output ratios and does not accumulate tile drift.
                    dest = (left * out_width // width, top * out_height // height,
                            right * out_width // width, bottom * out_height // height)
                    if dest[2] <= dest[0] or dest[3] <= dest[1]:
                        continue
                    with image.crop(padded) as patch:
                        pixels = np.asarray(patch, dtype=np.float32) / 255.0
                    tensor = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0).to(device=model.device, dtype=model.dtype)
                    predicted = model(tensor)
                    raw = predicted[0].detach().float().clamp_(0, 1).permute(1, 2, 0).cpu().numpy()
                    raw = (raw * 255.0).round().astype(np.uint8)
                    # Release every tensor before preparing the next tile.
                    del tensor, predicted
                    source_box = (
                        dest[0] * width * MODEL_SCALE / out_width - padded[0] * MODEL_SCALE,
                        dest[1] * height * MODEL_SCALE / out_height - padded[1] * MODEL_SCALE,
                        dest[2] * width * MODEL_SCALE / out_width - padded[0] * MODEL_SCALE,
                        dest[3] * height * MODEL_SCALE / out_height - padded[1] * MODEL_SCALE,
                    )
                    with Image.fromarray(raw) as enlarged, enlarged.resize(
                        (dest[2] - dest[0], dest[3] - dest[1]),
                        Image.Resampling.LANCZOS, box=source_box,
                    ) as tile:
                        output.paste(tile, (dest[0], dest[1]))
                    completed += 1
                    if progress:
                        progress(f"AI upscaling with Real-ESRGAN: tile {completed}/{total}", tile=completed, tiles=total)
        return output
    except BaseException:
        output.close()
        raise


def upscale_image(image, checkpoint_path, *, target_size, device="cpu", tile_size=256, progress=None):
    """Return an exact-size PIL image, using learned restoration for enlargement.

    Both dimensions are bounded to 8192. Ratios above 4x use multiple learned
    passes, each stitched directly to at most the target size. Alpha is resized
    separately instead of being hallucinated. Small/native exports need no model.
    """
    from PIL import Image

    if not isinstance(target_size, (tuple, list)) or len(target_size) != 2:
        raise ValueError("AI upscale target_size must contain width and height")
    width = _bounded_integer(target_size[0], "output width", 1, MAX_DIMENSION)
    height = _bounded_integer(target_size[1], "output height", 1, MAX_DIMENSION)
    tile_size = _bounded_integer(tile_size, "tile_size", 16, 1024)
    if device not in ("cpu", "cuda"):
        raise ValueError("AI upscale device must be cpu or cuda")
    target_size = (width, height)
    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        raise ValueError("AI upscale input dimensions exceed 8192")
    if target_size == image.size:
        return image.copy()
    if width <= image.width and height <= image.height:
        return image.resize(target_size, Image.Resampling.LANCZOS)

    model = None
    current = None
    alpha = None
    try:
        if progress:
            progress("Loading verified Real-ESRGAN AI upscaler", upscaler=MODEL_ID)
        model = load_upscale_model(checkpoint_path, device=device)
        if "A" in image.getbands() or "transparency" in image.info:
            with image.convert("RGBA") as rgba:
                alpha = rgba.getchannel("A").resize(target_size, Image.Resampling.LANCZOS)
        current = image.convert("RGB")
        # If a custom aspect ratio shrinks one axis, reduce that axis first.
        # Every learned pass then has ratios from 1x to 4x, keeping its sampling
        # support within the fixed tile halo even for a very narrow export.
        reduced_size = (min(current.width, width), min(current.height, height))
        if reduced_size != current.size:
            reduced = current.resize(reduced_size, Image.Resampling.LANCZOS)
            current.close()
            current = reduced
        pass_index = 0
        while current.size != target_size:
            next_size = (min(width, current.width * MODEL_SCALE), min(height, current.height * MODEL_SCALE))
            pass_index += 1
            if progress:
                progress(f"AI upscale pass {pass_index}: {next_size[0]} × {next_size[1]}",
                         upscaler=MODEL_ID, passIndex=pass_index, outputWidth=next_size[0], outputHeight=next_size[1])
            enlarged = _upscale_pass(current, model, next_size, tile_size=tile_size, progress=progress)
            current.close()
            current = enlarged
        if alpha is not None:
            current.putalpha(alpha)
        result, current = current, None
        return result
    finally:
        if current is not None:
            current.close()
        if alpha is not None:
            alpha.close()
        if model is not None:
            # The short-lived upscaler must not retain memory needed by the next
            # generation. Main pipeline cleanup remains the caller's responsibility.
            model.to(device="cpu")
            del model
            if device == "cuda":
                import torch
                torch.cuda.empty_cache()
