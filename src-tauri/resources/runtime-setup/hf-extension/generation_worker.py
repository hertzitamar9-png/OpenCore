"""Offline, staged Budget3D/IDOL inference. Imported by CPU tests without importing torch."""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))
from hf_download import atomic_json
from hf_generation import MODEL, IDOL, validate_request, verify_ply
from hf_runtime import _inside


def sha256(file):
    digest = hashlib.sha256()
    with Path(file).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


class NativeBackend:
    def __init__(self, request, progress):
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                          TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
        vendor = HERE / 'budget3d-runtime'
        os.environ.update(IDOL_ROOT=str(vendor / 'idol-sdk'), BUDGET3D_DINOV3=request['components']['dinov3'],
                          BUDGET3D_FLUX2_VAE=request['components']['flux2'])
        sys.path.insert(0, str(vendor))
        import torch
        self.torch, self.request, self.progress = torch, request, progress
        if not torch.cuda.is_available():
            raise RuntimeError('Budget3D/IDOL requires a compatible CUDA GPU. The checkpoint remains installed; no GPU was available for this job.')
        free, total = torch.cuda.mem_get_info()
        if free < 3 * 1024**3:
            raise RuntimeError(f'Not enough free GPU memory to start 3D generation ({free / 1024**3:.1f} GiB available). Unload other models or close GPU-heavy applications, then retry.')
        torch.set_num_threads(2)
        self.device = 'cuda'

    def release(self):
        gc.collect()
        self.torch.cuda.empty_cache()

    def encode(self, image, weights, settings):
        from budget3d.sampling.sampler import ImagePreprocessor
        self.progress('Removing the background and encoding the input image')
        processor = ImagePreprocessor(str(weights), self.device, settings['imageMode'])
        # A CPU copy lets both large encoders leave VRAM before denoising starts.
        with self.torch.inference_mode():
            prepared, conditioning = processor(str(image))
            conditioning = conditioning.to('cpu')
        del processor
        self.release()
        return prepared, conditioning

    def sample(self, conditioning, weights, settings):
        from budget3d.models.load import load_budget3d
        from budget3d.sampling.sampler import sample
        self.progress('Loading the 3D denoiser; image encoders have been released')
        if self.request['repository'] == MODEL[0]:
            checkpoint = Path(self.request['modelDirectory']) / 'budget3d_idol.safetensors'
            model, _ = load_budget3d(str(checkpoint), device='cpu', weights_dir=str(weights))
            model = model.to(self.device).bind_geometry()
        else:
            from budget3d.models.load import load_student
            # At zero initialization the full dense Budget3D wrapper is the released IDOL denoiser.
            model = load_student(str(weights), device='cpu').to(self.device).bind_geometry().eval().requires_grad_(False)
        conditioning = conditioning.to(self.device)
        with self.torch.inference_mode():
            latent, camera, layout = sample(model, conditioning, budget=settings['budget'], steps=settings['steps'],
                cfg=settings['guidanceScale'], rescale_t=settings['rescaleT'], seed=settings['seed'], device=self.device,
                progress_callback=lambda step, total: self.progress(f'Generating 3D · step {step} of {total}', step=step, total=total))
            latent = latent.to('cpu')
            camera = camera.to('cpu')
        del model, conditioning, layout
        self.release()
        return latent, camera

    def decode(self, latent, weights, settings, destination):
        from budget3d.models.load import load_decoders, decode_gaussians
        self.progress('Decoding Gaussian splats; the denoiser has been released')
        dec, dec_gs, config = load_decoders(str(weights), self.device)
        with self.torch.inference_mode():
            gaussian = decode_gaussians(latent.to(self.device), dec, dec_gs, config, settings['numGaussians'])
            gaussian.save_ply(str(destination))
        del dec, dec_gs, gaussian
        self.release()


def generate(request, output, *, backend_factory=NativeBackend):
    validated = validate_request(request)
    if (request.get('repository'), request.get('revision')) not in (MODEL, IDOL): raise ValueError('Unreviewed 3D checkpoint')
    root, output = Path(request['modelRoot']).resolve(), Path(output).resolve()
    _inside(output, root / 'studio/hub-3d-jobs')
    image = _inside(validated['settings']['inputPath'], output)
    _inside(request['modelDirectory'], root / 'models/hub')
    for key in ('idol', 'dinov3', 'flux2'): _inside(request['components'][key], root / 'models/hub')
    if not image.is_file(): raise ValueError('The job input image is missing')
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 40_000_000
    with Image.open(image) as test:
        if test.width * test.height > Image.MAX_IMAGE_PIXELS or min(test.size) < 16: raise ValueError('Choose an image between 16 pixels and 40 megapixels')
        test.verify()
    cancel = output.parent / 'cancel'
    def progress(stage, **values):
        if cancel.exists(): raise RuntimeError('3D generation cancelled')
        atomic_json(output / 'progress.json', {'stage': stage, **values})
    started = time.monotonic()
    backend = backend_factory(request, progress)
    weights = Path(request['components']['idol'])
    prepared, conditioning = backend.encode(image, weights, validated['settings'])
    preview = output / 'prepared-input.png'
    prepared.save(preview)
    latent, camera = backend.sample(conditioning, weights, validated['settings'])
    del conditioning
    ply = output / 'object.ply'
    backend.decode(latent, weights, validated['settings'], ply)
    vertices = verify_ply(ply)
    proof = {'modelId': validated['modelId'], 'revision': request['revision'], 'repository': request['repository'],
             'inferenceVerified': True, 'outputKind': 'gaussian-splat', 'ply': str(ply), 'inputPreview': str(preview),
             'vertices': vertices, 'inputSha256': sha256(image), 'outputSha256': sha256(ply),
             'settings': validated['settings'], 'elapsedSeconds': time.monotonic() - started}
    atomic_json(output / 'generation-result.json', proof)
    progress('3D generation complete', vertices=vertices)
    return proof


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(json.loads(args.request.read_bytes()), args.output)
    except Exception as error:
        message = str(error)
        if 'out of memory' in message.lower():
            message = '3D generation ran out of GPU memory. Unload other models or close GPU-heavy applications and retry. ' + message[:800]
        atomic_json(args.output / 'generation-error.json', {'error': message[:1800], 'inferenceVerified': False})
        print(message[:1800], file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
