"""Streaming identities and atomic receipts for this owned run only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .errors import checkpoint


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_identity(path,cancel=None):
    path=Path(path).resolve()
    before=path.stat()
    value=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(4*1024*1024),b''):
            checkpoint(cancel)
            value.update(chunk)
    after=path.stat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('A file changed while computing its identity: '+path.name)
    return {'sha256':value.hexdigest(),'bytes':after.st_size,'mtimeNs':after.st_mtime_ns}


def atomic_json(path,value):
    path=Path(path);temporary=None
    try:
        with tempfile.NamedTemporaryFile('wb',prefix='.receipt-',suffix='.tmp',dir=path.parent,delete=False) as output:
            temporary=Path(output.name);output.write(canonical(value)+b'\n');output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
