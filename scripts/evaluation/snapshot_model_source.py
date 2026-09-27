"""Keep exact small model source files alongside a recorded runtime identity."""
import argparse
import json
from pathlib import Path
import shutil

from benchmark_capture import file_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--identity', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f'Refusing to replace source snapshot: {args.output}')
    identity = json.loads(args.identity.read_text())
    files = [item for item in identity['runtime_files']
             if Path(item['path']).suffix.lower() in ('.py', '.cpp', '.json')
             or Path(item['path']).name == 'fusioncore.dll']
    args.output.mkdir(parents=True)
    records = []
    for index, item in enumerate(files):
        source = Path(item['path'])
        if source.stat().st_size != item['bytes'] or file_hash(source) != item['sha256']:
            raise ValueError(f'Model source changed since identity recording: {source}')
        target = args.output / f'{index:02}-{source.name}'
        shutil.copyfile(source, target)
        if file_hash(target) != item['sha256']:
            raise ValueError('Source snapshot copy failed its hash check')
        records.append({**item, 'snapshot_file': target.name})
    (args.output / 'snapshot-manifest.json').write_text(json.dumps({
        'identity_sha256': file_hash(args.identity), 'files': records,
        'vendor_binary_note': 'Large unchanged runtime libraries remain identified by their pinned file hashes and build source revision.'
    }, indent=2) + '\n')
    print(json.dumps({'files': len(records), 'bytes': sum(row['bytes'] for row in records),
                      'snapshot': str(args.output)}))


if __name__ == '__main__':
    main()
