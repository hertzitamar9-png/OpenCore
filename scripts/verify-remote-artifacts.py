"""Qualify removable checkpoint copies against immutable Hugging Face LFS hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--local', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if len(args.revision) != 40:
        parser.error('An immutable 40-character revision is required')
    url = f'https://huggingface.co/api/models/{args.repo}/revision/{args.revision}?blobs=true'
    with urllib.request.urlopen(url, timeout=60) as response:
        info = json.load(response)
    if info['sha'] != args.revision:
        raise RuntimeError('Hub returned a different revision')
    root = args.local.resolve(strict=True)
    files = {file['rfilename']: file for file in info['siblings']}
    evidence = []
    for path in root.rglob('*'):
        if not path.is_file() or path.suffix not in ('.safetensors', '.gguf', '.bin'):
            continue
        remote = files.get(path.relative_to(root).as_posix())
        lfs = (remote or {}).get('lfs') or {}
        if not lfs.get('sha256') or path.stat().st_size != lfs['size']:
            raise RuntimeError(f'No exact remote weight copy: {path}')
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if sha != lfs['sha256']:
            raise RuntimeError(f'Remote hash differs: {path}')
        evidence.append({'path': str(path), 'expectedBytes': path.stat().st_size,
                         'sha256': sha, 'repo': args.repo, 'revision': args.revision,
                         'filename': remote['rfilename'], 'verified': True})
    if not evidence:
        raise RuntimeError('No checkpoint files were verified')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'files': evidence}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'verified_files': len(evidence), 'bytes': sum(file['expectedBytes'] for file in evidence)}))


if __name__ == '__main__':
    main()
