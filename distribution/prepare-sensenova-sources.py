"""Bundle exact publisher source, without downloading or executing model weights."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / 'distribution/runtime-research/sensenova-loop'
TARGET = ROOT / 'src-tauri/resources/text-runtime/sensenova_loop'
SNAPSHOT = ROOT / 'distribution/hf-research/requested-models/sensenova--SenseNova-Loop-9B.api.json'


def main():
    metadata = json.loads(SNAPSHOT.read_bytes())
    assert metadata['id'] == 'sensenova/SenseNova-Loop-9B'
    assert metadata['sha'] == '329a65f701ba8c48b73c30d0cbaa6f0449733673'
    source = RESEARCH / 'modeling_sensenova_loop.py'
    raw = source.read_bytes()
    pin = next(row for row in metadata['siblings'] if row['rfilename'] == source.name)
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    assert blob == pin['blobId'] and len(raw) == pin['size']
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, TARGET / source.name)
    shutil.copyfile(RESEARCH / 'LICENSE', TARGET / 'LICENSE')
    (TARGET / '__init__.py').write_text('"""Reviewed app-owned SenseNova Loop implementation."""\n', encoding='utf-8')
    (TARGET / 'upstream.json').write_text(json.dumps({
        'repository': metadata['id'], 'revision': metadata['sha'], 'license': 'Apache-2.0',
        'source': source.name, 'gitBlobSha1': blob, 'sha256': hashlib.sha256(raw).hexdigest(),
        'publicCodeRepository': 'sensenova/SenseNova-Loop-9B-Preview',
        'publicCodeRevision': '89d7970926e215f8da61726700cd026ccffbb6cb',
        'identityEvidence': 'The public publisher source has the identical Git blob advertised by the full release.',
        'modified': False, 'weightsDownloaded': False, 'inferenceVerified': False,
    }, indent=2) + '\n', encoding='utf-8')
    (TARGET / 'OPENCORE-NOTICE.txt').write_text(
        'SenseNova Loop source is bundled under Apache-2.0.\n'
        'The model implementation is byte-for-byte publisher code; local adapters live in protocol.py.\n'
        'No code from a downloaded model directory is imported.\n', encoding='utf-8')
    print(json.dumps({'files': 5, 'sourceSha256': hashlib.sha256(raw).hexdigest(), 'inferenceVerified': False}))


if __name__ == '__main__':
    main()
