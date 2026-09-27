"""Keep exact small model source files alongside a recorded runtime identity."""
import argparse
import json
from pathlib import Path
import shutil

from benchmark_capture import file_hash


def snapshot(identity_path, output):
    identity_path, output = Path(identity_path), Path(output)
    if output.exists():
        raise FileExistsError(f'Refusing to replace source snapshot: {output}')
    identity = json.loads(identity_path.read_text(encoding='utf-8'))
    files = [item for item in identity['runtime_files']
             if Path(item['path']).suffix.lower() in ('.py', '.cpp', '.h', '.hpp', '.json', '.txt', '.md')
             or Path(item['path']).name.lower() in ('fusioncore.dll', 'twincore.dll')]
    for item in files:
        source = Path(item['path'])
        if source.stat().st_size != item['bytes'] or file_hash(source) != item['sha256']:
            raise ValueError(f'Model source changed since identity recording: {source}')
    existing_parent = output.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    required = sum(item['bytes'] for item in files) + 1_048_576
    if shutil.disk_usage(existing_parent).free < 200_000_000_000 + required:
        raise ValueError('Source snapshot would violate the 200 GB free-space reserve')
    output.mkdir(parents=True)
    records = []
    for index, item in enumerate(files):
        source = Path(item['path'])
        if source.stat().st_size != item['bytes'] or file_hash(source) != item['sha256']:
            raise ValueError(f'Model source changed since identity recording: {source}')
        target = output / f'{index:02}-{source.name}'
        shutil.copyfile(source, target)
        if file_hash(target) != item['sha256']:
            raise ValueError('Source snapshot copy failed its hash check')
        records.append({**item, 'snapshot_file': target.name})
    (output / 'snapshot-manifest.json').write_text(json.dumps({
        'identity_sha256': file_hash(identity_path), 'files': records,
        'vendor_binary_note': 'Large unchanged runtime libraries remain identified by their pinned file hashes and build source revision.'
    }, indent=2) + '\n', encoding='utf-8')
    return {'files': len(records), 'bytes': sum(row['bytes'] for row in records), 'snapshot': str(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.identity, args.output)))


if __name__ == '__main__':
    main()
