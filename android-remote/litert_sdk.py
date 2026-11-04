"""Verified official LiteRT-LM SDK inputs shared by the Android build and tests."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def inputs():
    vendor = ROOT/'vendor/litertlm'
    manifest = json.loads((vendor/'manifest.json').read_text(encoding='utf-8'))
    for name, record in manifest['files'].items():
        file = vendor/name
        if not file.is_file() or file.stat().st_size != record['bytes'] or hashlib.sha256(file.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('The pinned LiteRT-LM dependency is missing or changed: '+name)
    return vendor, manifest, [vendor/name for name in manifest['files'] if name.endswith('.jar')]


def resources(archive, jars):
    """Keep Kotlin reflection metadata and service providers; omit Java signatures."""
    collected = {}
    for jar in jars:
        with zipfile.ZipFile(jar) as source:
            for name in source.namelist():
                upper = name.upper()
                if name.endswith(('/', '.class')) or upper == 'META-INF/MANIFEST.MF' or upper.startswith('META-INF/VERSIONS/') or upper.endswith(('.SF', '.RSA', '.DSA', '.EC')):
                    continue
                data = source.read(name)
                if name.startswith('META-INF/services/'):
                    lines = (collected.get(name, b'')+b'\n'+data).decode('utf-8').splitlines()
                    collected[name] = ('\n'.join(dict.fromkeys(line.strip() for line in lines if line.strip()))+'\n').encode()
                elif name not in collected:
                    collected[name] = data
                elif collected[name] != data and not name.startswith('META-INF/'):
                    raise ValueError('Conflicting LiteRT-LM runtime resource: '+name)
    for name, data in collected.items():
        archive.writestr(name, data)
