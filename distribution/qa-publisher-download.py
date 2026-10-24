"""Prepare isolated real publisher checkpoints for generation acceptance checks."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_catalog import build_plan, stable_id
from hf_download import Downloader, HubClient


root = HERE / '.output/publisher-live'
root.mkdir(parents=True, exist_ok=True)
catalog = json.loads((HERE.parent / 'src-tauri/resources/model-catalog.json').read_text())
artifacts = {a['id']: a for a in catalog['artifacts']}
downloader = Downloader(root, HubClient())
parser = argparse.ArgumentParser()
parser.add_argument('models', nargs='*')
selected = parser.parse_args().models or ['flint-nvfp4-gguf', 'raon-1-mini-iq4-xs']
for model_id in selected:
    if model_id == 'flint-iq2-xxs':
        metadata = json.loads((HERE / '.output/runtime-research/bartowski-flint.api.json').read_text())
        entry = next(v for v in build_plan(metadata)['variants'] if v['quantization'] == 'IQ2_XXS')
        entry['contextTokens'] = 8192
        pin = entry['files'][0]
    else:
        model = next(m for m in catalog['models'] if m['id'] == model_id)
        source = artifacts[model['weightArtifacts'][0]]
        key = 'gguf:' + source['filename']
        pin = {'repository': source['repo'], 'revision': source['revision'], 'filename': source['filename'],
               'bytes': source['bytes'], 'sha256': source['sha256'], 'role': 'model', 'pinned': True}
        entry = {'id': stable_id(source['repo'], key), 'repository': source['repo'], 'revision': source['revision'],
                 'variantKey': key, 'format': 'gguf', 'quantization': model['precision'], 'files': [pin],
                 'bytes': pin['bytes'], 'installable': True, 'modelType': 'flint' if model_id.startswith('flint') else 'raon1_mini',
                 'contextTokens': 8192, 'runtime': {'adapter': 'gguf'}}
    if shutil.disk_usage(root).free < pin['bytes'] + 100_000_000_000:
        raise RuntimeError('Verification would consume reserved free disk space')
    (root / (model_id + '-entry.json')).write_text(json.dumps(entry, indent=2))
    last = [0.0]
    def report(value):
        now = time.monotonic()
        if now - last[0] >= 20 or value['stage'] in ('installed', 'verified'):
            last[0] = now
            print(json.dumps({'model': model_id, **value}), flush=True)
    receipt = downloader.install(entry, threading.Event(), report)
    entry.update(localDirectory=receipt['localDirectory'], receiptPath=receipt['receiptPath'], status='installed')
    (root / (model_id + '-entry.json')).write_text(json.dumps(entry, indent=2))
print('Both immutable publisher checkpoints are ready for real generation checks', flush=True)
