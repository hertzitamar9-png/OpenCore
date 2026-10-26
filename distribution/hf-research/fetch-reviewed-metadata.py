"""Refresh explicitly named research inputs; never download weights or execute repo code."""
import concurrent.futures
import json
from pathlib import Path
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

OUTPUT = Path(__file__).parent / 'requested-models'


def fetch(repository):
    def read(path):
        with urlopen(Request('https://huggingface.co/' + path, headers={'User-Agent': 'OpenCore-catalog-research/1'}), timeout=25) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024: raise ValueError('Metadata too large')
        return data
    encoded = quote(repository, safe='/')
    metadata = json.loads(read('api/models/' + encoded + '/revision/main?blobs=true'))
    if metadata['id'].lower() != repository.lower(): raise ValueError('Repository identity mismatch')
    prefix = encoded + '/resolve/' + metadata['sha'] + '/'
    if any(item['rfilename'] == 'config.json' for item in metadata['siblings']):
        try: metadata['config'] = {**metadata.get('config', {}), **json.loads(read(prefix + 'config.json'))}
        except Exception: pass
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / (repository.replace('/', '--'))
    target.with_suffix(target.suffix + '.api.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    try: target.with_suffix(target.suffix + '.README.md').write_bytes(read(prefix + 'README.md'))
    except Exception: pass
    return {'repository': repository, 'revision': metadata['sha'], 'files': len(metadata['siblings']),
            'base': (metadata.get('cardData') or {}).get('base_model')}


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for value in pool.map(fetch, sys.argv[1:]): print(json.dumps(value))
