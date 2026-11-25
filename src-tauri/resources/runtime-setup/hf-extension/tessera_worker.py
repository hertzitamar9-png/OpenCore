"""Run one offline Tessera job using the publisher's pinned architecture and sampler."""
import json
import os
from pathlib import Path
import secrets
import sys


def write_json(path, value):
    partial = path.with_suffix('.tmp')
    partial.write_text(json.dumps(value), encoding='utf-8')
    partial.replace(path)


def generate(payload, output):
    os.environ.update(CUDA_VISIBLE_DEVICES='-1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    sys.path.insert(0, payload['source'])
    import torch
    from PIL import Image
    from tessera import ModelConfig, Palette, Tessera
    from tessera.sampler import sample_iter
    from tessera.text_encoder import FrozenTextEncoder
    from tessera.viz import tokens_to_images

    torch.set_num_threads(min(2, os.cpu_count() or 1))
    checkpoint = torch.load(payload['checkpoint'], map_location='cpu', weights_only=True)
    config = ModelConfig.from_dict(checkpoint['model_config'])
    palette = Palette.from_dict(checkpoint['palette'])
    if config.grid_size != 16 or checkpoint['text_encoder'] != 'openai/clip-vit-base-patch32':
        raise ValueError('Unexpected Tessera architecture or text encoder')
    model = Tessera(config, torch.from_numpy(palette.lab), torch.from_numpy(palette.colors))
    model.load_state_dict(checkpoint['ema'], strict=True)
    model.eval().requires_grad_(False)
    del checkpoint
    encoder = FrozenTextEncoder(payload['encoder'], config.text_len, 'cpu', torch.float32)
    request = payload['request']
    settings = request['settings']
    count = settings.get('numImages', 1)
    steps = settings.get('steps', 16)
    seed = settings.get('seed', secrets.randbits(32))
    generator = torch.Generator(device='cpu').manual_seed(seed)
    with torch.inference_mode():
        states, pooled = encoder.encode([request['prompt']] * count)
        tokens = None
        for step in sample_iter(model, states, pooled, steps=steps, cfg_scale=settings.get('guidanceScale', 3.0),
                                amp_dtype=torch.float32, generator=generator):
            tokens = step.tokens
            write_json(output / 'progress.json', {'stage': 'Drawing sprite', 'step': step.index + 1, 'steps': steps})
    if tokens is None or tokens.shape != (count, 256) or tokens.min() < 0 or tokens.max() >= len(palette.colors):
        raise ValueError('Tessera returned an incomplete sprite')
    for i, image in enumerate(tokens_to_images(tokens, palette)):
        path = output / f'sprite-{i + 1}.png'
        image.save(path, format='PNG')
        with Image.open(path) as saved:
            if saved.mode != 'RGBA' or saved.size != (16, 16):
                raise ValueError('Invalid Tessera sprite')
            saved.verify()
    write_json(output / 'result.json', {'modelId': request['modelId'], 'count': count, 'seed': seed})


if __name__ == '__main__':
    generate(json.loads(Path(sys.argv[1]).read_bytes()), Path(sys.argv[2]))
