"""Refresh reviewed inference sources and immutable component pins. Never fetch weights."""
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'src-tauri/resources/runtime-setup/hf-extension/budget3d-runtime'
RESEARCH = ROOT / 'distribution/runtime-research/budget3d'
sys.path.insert(0, str(DEST.parent))
from hf_catalog import _pin


def download(url, limit=2 * 1024**2):
    with urlopen(Request(url, headers={'User-Agent': 'OpenCore pinned inference source preparation'}), timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Source file exceeded its size limit')
    return data


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    provenance = {'schema': 1, 'sources': [], 'localChanges': []}
    for repo, revision, prefix, selected in [
        ('BudgetAny/Budget3D', '95bbeab46063375d560f9b20fa7b9d9fedb078f7', '', [
            'LICENSE', 'budget3d/__init__.py', 'budget3d/upstream.py', 'budget3d/octree.py',
            'budget3d/models/__init__.py', 'budget3d/models/adapters.py', 'budget3d/models/denoiser.py',
            'budget3d/models/encoders.py', 'budget3d/models/load.py',
            'budget3d/sampling/__init__.py', 'budget3d/sampling/sampler.py']),
        ('speridlabs/idol-sdk', '956439f5e664a7e8c5db25a8055e72d6365aafa5', 'idol-sdk/', [
            'LICENSE', 'idol/__init__.py', 'idol/camera.py', 'idol/conditioning.py', 'idol/device.py',
            'idol/gaussian.py', 'idol/preprocess.py', 'idol/pipeline.py', 'idol/sample.py', 'idol/sampler.py', 'idol/models/__init__.py',
            'idol/models/birefnet.py', 'idol/models/decoder.py', 'idol/models/dit.py', 'idol/models/layers.py']),
    ]:
        files = []
        for name in selected:
            data = download(f'https://raw.githubusercontent.com/{repo}/{revision}/{name}')
            target = DEST / (prefix + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append({'path': prefix + name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        provenance['sources'].append({'repository': repo, 'revision': revision, 'files': files})
    sampler = DEST / 'budget3d/sampling/sampler.py'
    source = sampler.read_text(encoding='utf-8')
    source = source.replace('device="cuda", warmup_steps=0, noise=None):',
                            'device="cuda", warmup_steps=0, noise=None, progress_callback=None):')
    source = source.replace('        cam = cam - (t - t_prev) * v_cam\n',
                            '        cam = cam - (t - t_prev) * v_cam\n        if progress_callback is not None: progress_callback(i + 1, steps)\n')
    if 'progress_callback(i + 1, steps)' not in source: raise ValueError('Upstream sampler changed')
    sampler.write_text(source, encoding='utf-8', newline='\n')
    provenance['localChanges'].append({'path': 'budget3d/sampling/sampler.py',
        'description': 'Optional progress/cancellation callback after each unchanged Euler step.',
        'sha256': hashlib.sha256(sampler.read_bytes()).hexdigest()})
    (DEST / 'upstream.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    components = []
    for key, repo, names, license in [
        ('idol', 'speridlabs/idol', ['config.json', 'birefnet.safetensors', 'denoiser.safetensors',
                                  'decoder.safetensors', 'decoder_gs.safetensors'], 'apache-2.0'),
        ('dinov3', 'facebook/dinov3-vith16plus-pretrain-lvd1689m',
         ['config.json', 'model.safetensors', 'preprocessor_config.json'], 'dinov3'),
        ('flux2', 'black-forest-labs/FLUX.2-klein-4B',
         ['vae/config.json', 'vae/diffusion_pytorch_model.safetensors'], 'apache-2.0'),
    ]:
        metadata = json.loads((RESEARCH / (repo.replace('/', '--') + '.api.json')).read_bytes())
        files = [_pin(repo, metadata['sha'], next(s for s in metadata['siblings'] if s['rfilename'] == name),
                      'model' if name.endswith('.safetensors') else 'support') for name in names]
        components.append({'id': key, 'repository': repo, 'revision': metadata['sha'],
                           'license': license, 'gated': bool(metadata.get('gated')), 'files': files,
                           'bytes': sum(p['bytes'] for p in files)})
    (DEST / 'components.json').write_text(json.dumps({'schema': 1, 'components': components}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'sourceFiles': sum(len(s['files']) for s in provenance['sources']),
                      'componentFiles': sum(len(c['files']) for c in components),
                      'componentBytes': sum(c['bytes'] for c in components)}))


if __name__ == '__main__':
    main()
