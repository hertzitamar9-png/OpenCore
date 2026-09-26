"""Complete the private source archive with restoration metadata and exact references."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import HfApi, CommitOperationAdd
from huggingface_hub.utils import disable_progress_bars

disable_progress_bars()

APP = Path(__file__).resolve().parents[1]
PROJECT = APP.parents[1]
REPO = 'Pita-Ai/OpenCore-source-archive-20260926'
api = HfApi()
assert api.model_info(REPO).private
local = PROJECT / 'release/base-seeds/OpenCore-APEX-Dequantised-HF-corrected-v1'
sources = {f'weights/OpenCore-APEX-Dequantised-HF-corrected-v1/{name}': (local / name).read_bytes()
    for name in ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'conversion-report.json']}
index = json.loads((PROJECT / 'artifacts/local-models-remote-index-20260926.json').read_text(encoding='utf-8-sig'))
uploaded = {entry['sha256']: entry['remote'] for entry in json.loads(
    (PROJECT / 'artifacts/unique-models-remote-20260926.json').read_text())['files']}
rows = {}
for entry in index['files']:
    remote = uploaded.get(entry['sha256']) or entry.get('remote')
    rows[entry['sha256']] = {'sha256': entry['sha256'], 'bytes': entry['bytes'],
        'remote': remote, 'verified_remote': remote is not None}
sources['weight-references.json'] = json.dumps(list(rows.values()), indent=2).encode()
sources['README.md'] = b'''# OpenCore source artifact archive

Private, exact copies of experimental source weights, adapters and converted
computer-use weights. This repository is an archive, not a newly trained model
or a quality benchmark. The APEX-dequantized source does not restore precision
discarded by its source quantization. Older bridge files are preserved research
artifacts and are not the untuned native LFM FusionCore coupling.

`weight-references.json` binds each artifact to an immutable revision and SHA-256.
`verified_remote: false` means an upload is still pending; do not remove that local
source until the immutable remote file hash and size have been checked.

The desktop Models tab installs current profiles separately from pinned upstream
or private Hub revisions. No weights or conversations are bundled in the app.
Public upstream weights remain in their original repositories; this archive avoids
making redundant copies of them. Original weight licenses and notices still apply.
Holo 3.1-0.8B is from Hcompany/Holo-3.1-0.8B (Apache-2.0); the classifier under
`weights/model` is the existing Reflex typed policy, not the Holo language model.
The classifier source remains available in the private OpenCore app repository.
'''
commit = api.create_commit(repo_id=REPO, operations=[CommitOperationAdd(path_in_repo=name,
    path_or_fileobj=data) for name, data in sorted(sources.items())],
    commit_message='Bind archived source tensors to restoration metadata and provenance')
remote = {entry.rfilename: entry for entry in api.model_info(REPO, revision=commit.oid, files_metadata=True).siblings}
for name, data in sources.items():
    entry = remote[name]
    assert entry.size == len(data)
    if entry.lfs:
        assert entry.lfs.sha256 == hashlib.sha256(data).hexdigest()
    else:
        assert entry.blob_id == hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
receipt = {'repo': REPO, 'revision': commit.oid, 'files_verified': len(sources),
    'all_weight_references_verified': all(row['verified_remote'] for row in rows.values())}
(APP / 'tests/evidence/source-archive-metadata-2026-09-26.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt))
