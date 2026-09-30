"""Create an exact PowerShell cleanup plan after proving immutable remote copies."""
import argparse
import hashlib
import json
from pathlib import Path
from huggingface_hub import HfApi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--uploads', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--include-pending', action='store_true')
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding='utf-8-sig'))
    uploads = json.loads(args.uploads.read_text(encoding='utf-8-sig'))['files']
    uploaded = {file['sha256']: file['remote'] for file in uploads}
    api, metadata, targets, pending = HfApi(), {}, [], []
    for file in inventory['files']:
        path = Path(file['path'])
        if not path.is_file():
            continue
        remote = uploaded.get(file['sha256']) or file.get('remote')
        if not remote:
            pending.append(str(path))
            continue
        if len(remote['revision']) != 40:
            raise ValueError('Remote revision must be immutable')
        key = remote['repo'], remote['revision']
        if key not in metadata:
            info = api.model_info(key[0], revision=key[1], files_metadata=True)
            assert info.sha == key[1]
            metadata[key] = {entry.rfilename: entry for entry in info.siblings}
        entry = metadata[key][remote['filename']]
        assert entry.lfs and entry.lfs.sha256 == file['sha256']
        assert entry.size == file['bytes'] == path.stat().st_size
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == file['sha256'], str(path)
        targets.append({'path': str(path), 'expectedBytes': file['bytes'], 'sha256': file['sha256'],
            'remote': remote, 'reason': 'Exact SHA256-matched immutable remote copy; user requested no local OpenCore weights'})
    if pending and args.include_pending:
        raise RuntimeError('Still waiting for verified remote copies: ' + ', '.join(pending))
    plan = {'allowedRoots': [r'C:\Users\hertz\Documents\Best ai model in the world\release',
            r'C:\Users\hertz\OpenCore', r'C:\Users\hertz\OpenCore-1M', r'C:\Users\hertz\Documents\OpenCoreFusion'],
        'protectedPaths': pending + [
            r'C:\Users\hertz\AppData\Roaming\ai.opencore.control-center',
            r'C:\Users\hertz\Documents\MinecraftCreator\minecraft_creator_training_kit\runs\mageflow\specialization-v16\full-backbone-streaming-v1\final-fp32-master.safetensors'],
        'minimumFreeGiB': 100_000_000_000 / 2**30, 'targets': targets}
    args.plan.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    print(json.dumps({'verified_targets': len(targets), 'pending': pending,
        'bytes_in_named_files': sum(file['expectedBytes'] for file in targets)}))


if __name__ == '__main__':
    main()
