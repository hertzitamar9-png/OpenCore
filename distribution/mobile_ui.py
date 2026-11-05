"""Package the same built OpenCore interface for authenticated mobile clients."""
import hashlib
import json
import re
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
    # Vite changes chunk names on ordinary frontend edits. Discover this build's
    # complete JS/CSS set, including lazy imports, instead of pinning old names.
    files = {'index.html': FILES['index.html'], 'opencore-logo.png': 'image/png'}
    for asset in sorted((source/'assets').rglob('*')):
        if asset.is_file() and asset.suffix in ('.js', '.css', '.png'):
            relative = asset.relative_to(source).as_posix()
            if not re.fullmatch(r'assets/[A-Za-z0-9_./-]+', relative) or '..' in relative.split('/'):
                raise ValueError('Invalid mobile UI path')
            files[relative] = {'.js': FILES['assets/index-CD0EH-dA.js'], '.css': 'text/css; charset=utf-8', '.png': 'image/png'}[asset.suffix]
    files.update({'phone/mobile-shim.js': 'text/javascript; charset=utf-8', 'phone/mobile.css': 'text/css; charset=utf-8'})
    if len(files) > 128: raise ValueError('Too many mobile UI assets')
    total = 0
    for relative, mime in files.items():
        origin = (HERE.parent/'public/opencore-logo.png' if relative == 'opencore-logo.png' else
                  HERE.parent/'android-remote/assets'/relative.removeprefix('phone/') if relative.startswith('phone/') else source/relative)
        if not origin.is_file(): raise ValueError('Missing built mobile interface asset: '+relative)
        data = origin.read_bytes()
        total += len(data)
        if not data or len(data) > 8 * 1024 * 1024 or total > 32 * 1024 * 1024: raise ValueError('Mobile interface asset is too large')
        destination = target/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.resolve() != destination.resolve(): shutil.copyfile(origin, destination)
        records[relative] = {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mime':mime}
    manifest = {'schema':1,'shellApi':1,'modes':['pc','phone'],'files':records}
    temporary = target/'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    temporary.replace(target/'manifest.json')
    return manifest


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('target',type=Path)
    args=parser.parse_args();print(json.dumps({'files':len(prepare(args.target)['files'])}))
