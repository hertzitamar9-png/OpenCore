"""Pinned complete checkpoints and digest helpers, with no framework imports."""
import hashlib
import json
from pathlib import Path

SOURCE_COMMIT = '42adf019f76013dac873b5b43950d54d5ab27216'
CHECKPOINTS = {
    'nanbeige': {'sha256': '93f884a2d8d6cafc5406df84be64f197a407889904b18db7c6e82fd35f2b0170', 'bytes': 3595603104},
    'k2': {'sha256': '2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232', 'bytes': 4161403264},
}


def file_digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
