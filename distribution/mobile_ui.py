"""Package the same built OpenCore interface for authenticated mobile clients."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
FILES = {
    'index.html': 'text/html; charset=utf-8',
    'assets/index-CD0EH-dA.js': 'text/javascript; charset=utf-8',
    'assets/App-kNA9sRUP.js': 'text/javascript; charset=utf-8',
    'assets/model-catalog-B8SfuDgQ.js': 'text/javascript; charset=utf-8',
    'assets/index-CsoxYKnK.css': 'text/css; charset=utf-8',
    'assets/App-C9duitZV.css': 'text/css; charset=utf-8',
    'opencore-logo.png': 'image/png',
}


def prepare(target, source=HERE/'candidate-dist'):
    target, source = Path(target), Path(source)
    target.mkdir(parents=True, exist_ok=True)
    records = {}
    for relative, mime in FILES.items():
        origin = HERE.parent/'public/opencore-logo.png' if relative == 'opencore-logo.png' else source/relative
        if not origin.is_file(): raise ValueError('Missing built mobile interface asset: '+relative)
        data = origin.read_bytes()
        if len(data) > 8 * 1024 * 1024: raise ValueError('Mobile interface asset is too large')
        destination = target/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.resolve() != destination.resolve(): shutil.copyfile(origin, destination)
        records[relative] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mime':mime}
    manifest = {'schema':1,'files':records}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('target',type=Path)
    args=parser.parse_args();print(json.dumps({'files':len(prepare(args.target)['files'])}))
