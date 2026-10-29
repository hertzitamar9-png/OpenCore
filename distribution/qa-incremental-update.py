"""Validate every addressed release file, including its decompressed SHA-256."""
import argparse,gzip,hashlib,json
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--nsis',type=Path,required=True);args=parser.parse_args()
root=args.directory
index=json.loads((root/'incremental-index.json').read_text(encoding='utf-8'))
manifest=json.loads((root/'package-manifest.json').read_text(encoding='utf-8'))
assert index['schema']==1 and index['version']==manifest['build'] and index['commit']==manifest['commit']
expected={entry['path']:(entry['bytes'],entry['sha256']) for entry in manifest['files']}
assert len(expected)==len(index['files'])
offset=0
with (root/'OpenCore.Update.data').open('rb') as payload:
    for entry in index['files']:
        assert entry['offset']==offset
        assert expected[entry['path']]==(entry['bytes'],entry['sha256'])
        data=payload.read(entry['compressedBytes']);offset+=len(data)
        assert len(data)==entry['compressedBytes']
        raw=gzip.decompress(data)
        assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256']
    assert not payload.read(1)
exe=root/'OpenCore Update.exe'
proof={'passed':True,'version':index['version'],'files':len(index['files']),'updateSha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'independentFileHashesVerified':True,'rangeTransactionsTested':True}
from incremental_smoke import run
proof.update(run(args.nsis))
(root/'incremental-verification.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
print(json.dumps(proof))
