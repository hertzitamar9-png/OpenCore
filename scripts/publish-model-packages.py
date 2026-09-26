"""Publish small runtime packages and immutable weight references, never user history."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import HfApi, CommitOperationAdd

APP = Path(__file__).resolve().parents[1]
api = HfApi()
catalog = json.loads((APP / 'src-tauri/resources/model-catalog.json').read_text())
packages = [
    ('Pita-Ai/OpenCore-LFM-DualCore-FusionCore-Q8', 'lfm',
     ['dualcore-kv', 'dualcore-echo', 'fusioncore-kv', 'fusioncore-echo']),
    ('Pita-Ai/OpenCore-DuoCore-K2-Nanbeige-Q6', 'doucode', ['doucode']),
    ('Pita-Ai/OpenCore-TwinCore-experimental', 'fusion', []),
]
receipts = []
for repo, directory, ids in packages:
    api.create_repo(repo, repo_type='model', private=True, exist_ok=True)
    assert api.model_info(repo).private, 'Model packages must remain private'
    root = APP / 'src-tauri/resources' / directory
    files = [file for file in root.rglob('*') if file.is_file()
             and not any(part in ('runtime', 'build', '__pycache__') for part in file.relative_to(root).parts)
             and file.suffix in ('.py', '.md', '.json', '.cpp', '.txt', '.dll')]
    models = [model for model in catalog['models'] if model['id'] in ids]
    used = {artifact for model in models for artifact in model['artifacts']}
    references = [artifact for artifact in catalog['artifacts'] if artifact['id'] in used]
    contents = {str(file.relative_to(root)).replace('\\', '/'): file.read_bytes() for file in files}
    # The old TwinCore package is source only. Its manifest contains full public
    # checkpoint references and hashes, with the untrained status in README.
    contents['weight-references.json'] = json.dumps({'models': models, 'artifacts': references,
        'app_repository': 'https://github.com/hertzitamar9-png/OpenCore',
        'weights_embedded': False}, indent=2).encode()
    readme = contents.get('README.md', b'').decode()
    if directory == 'lfm':
        header = '---\nlicense: other\nlicense_name: lfm-open-license-1.0\nlicense_link: https://huggingface.co/LiquidAI/LFM2.5-2.6B/raw/main/LICENSE\npipeline_tag: text-generation\ntags:\n- gguf\n- opencore\n---\n\n'
    else:
        header = ''
    contents['README.md'] = (header + readme + '\n\nThis private repository stores the runtime package and immutable upstream weight references. '
        'Install weights explicitly in the OpenCore Models tab. It contains no conversations or personal files. '
        'The app repository bundles the pinned Windows inference dependencies.\n').encode()
    commit = api.create_commit(repo_id=repo,
        operations=[CommitOperationAdd(path_in_repo=path, path_or_fileobj=data) for path, data in sorted(contents.items())],
        commit_message='Publish pinned OpenCore runtime package and weight references')
    remote = {file.rfilename: file for file in api.model_info(repo, revision=commit.oid, files_metadata=True).siblings}
    for path, data in contents.items():
        file = remote[path]
        assert file.size == len(data), path
        if file.lfs:
            assert file.lfs.sha256 == hashlib.sha256(data).hexdigest(), path
        else:
            blob = f'blob {len(data)}\0'.encode() + data
            assert file.blob_id == hashlib.sha1(blob).hexdigest(), path
    receipt = {'repo': repo, 'revision': commit.oid, 'files': len(contents),
               'bytes': sum(len(data) for data in contents.values()), 'verified': True}
    receipts.append(receipt)
    print(json.dumps(receipt), flush=True)
(APP / 'tests/evidence/model-packages-remote-2026-09-26.json').write_text(json.dumps(receipts, indent=2))
