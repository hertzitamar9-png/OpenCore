"""Read upstream metadata without downloading weights. Keep research reproducible."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request

REPOS = [
    'ukisai/Swift-1.5-Qwen3.8-27B-GSQ-RCO-GGUF',
    'peculiar-ragdoll/Dirk-Qwen3.8-27B-GGUF',
    'trymirai/Qwen3.8-27B-S-experimental',
    'bottlecapai/ThinkingCap-Qwen3.8-27B',
    'DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF',
    'Qwen/Qwen-Image-2.1', 'unsloth/Qwen-Image-2.1-GGUF',
    'Hcompany/Holo4-27B', 'stabilityai/stable-point-aware-3d',
    'TencentARC/Pixal3D', 'ModelsLab/3D-Animation-Diffusion',
    'tencent/HY-Motion-1.0', 'tencent/HY-Motion-1.0-Lite',
    'Hcompany/Holo1.5-3B', 'ByteDance-Seed/UI-TARS-1.5-7B',
    'stabilityai/TripoSR',
]
DATASETS = ['openai/gsm8k', 'XiaomiMiMo/MiMo-V2.6-RL-oss', 'openbmb/UltraData-Code']

def inspect(item):
    kind, repo = item
    try:
        with urllib.request.urlopen(f'https://huggingface.co/api/{kind}/{repo}?blobs=true', timeout=45) as response:
            info = json.load(response)
        sha = info['sha']
        prefix = 'datasets/' if kind == 'datasets' else ''
        try:
            with urllib.request.urlopen(f'https://huggingface.co/{prefix}{repo}/resolve/{sha}/README.md', timeout=30) as response:
                readme = response.read(200_000).decode('utf-8')
        except Exception as error:
            readme = f'Readme unavailable: {error}'
        return {'repo': repo, 'kind': kind, 'revision': sha, 'card': info.get('cardData'),
                'files': info.get('siblings'), 'gated': info.get('gated'), 'readme': readme}
    except Exception as error:
        return {'repo': repo, 'kind': kind, 'error': str(error)}

if __name__ == '__main__':
    out = Path(__file__).resolve().parent / 'research' / 'platform-models.json'
    out.parent.mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(inspect, [('models', repo) for repo in REPOS] + [('datasets', repo) for repo in DATASETS]))
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
    for record in records:
        files = record.get('files') or []
        print(json.dumps({'repo': record['repo'], 'revision': record.get('revision'), 'error': record.get('error'),
                         'gated': record.get('gated'), 'files': len(files),
                         'weights': [{'name': file['rfilename'], 'bytes': file.get('size'), 'lfs': file.get('lfs', {}).get('sha256')}
                            for file in files if file['rfilename'].endswith(('.gguf', '.safetensors', '.ckpt', '.pt', '.pth', '.parquet'))][:22]}, ensure_ascii=False))
