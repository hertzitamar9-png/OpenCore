"""Resolve Windows 3.12 wheel metadata and hashes without downloading/installing wheels."""
import html
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'distribution/runtime-research/budget3d/pypi'
TARGET = ROOT / 'src-tauri/resources/runtime-setup/hf-extension/budget3d-runtime/python-lock.txt'
ENV = {**default_environment(), 'python_version': '3.12', 'python_full_version': '3.12.10',
       'sys_platform': 'win32', 'os_name': 'nt', 'platform_system': 'Windows', 'platform_machine': 'AMD64', 'extra': ''}
DIRECT = ['torch==2.9.1', 'torchvision==0.24.1', 'transformers==4.57.6', 'diffusers==0.36.0',
          'huggingface-hub==0.35.3', 'safetensors==0.8.0', 'tensordict==0.10.0', 'accelerate==1.14.0',
          'numpy==2.2.6', 'scipy==1.17.0', 'pillow==12.1.1', 'opencv-python-headless==4.13.0.92',
          'einops==0.8.2', 'tqdm==4.67.1', 'plyfile==1.1.5', 'pyyaml==6.0.3']


def read(url):
    with urlopen(Request(url, headers={'User-Agent': 'OpenCore dependency metadata resolution'}), timeout=40) as response:
        raw = response.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2:
        raise ValueError('Dependency metadata too large')
    return raw


def canonical(name): return name.lower().replace('_', '-').replace('.', '-')


def metadata(name, version=None):
    file = CACHE / (name + ('--' + version if version else '') + '.json')
    if not file.is_file():
        file.write_bytes(read('https://pypi.org/pypi/' + name + ('/' + version if version else '') + '/json'))
    return json.loads(file.read_bytes())


def windows_wheels(data):
    return [row for row in data['urls'] if not row.get('yanked') and row['filename'].endswith('.whl')
            and (row['filename'].endswith('-none-any.whl') or row['filename'].endswith('-win_amd64.whl'))
            and (re.search(r'-(?:py3|py2\.py3|cp312)-', row['filename'])
                 or re.search(r'-cp3(?:[7-9]|1[01])-abi3-', row['filename']))]


def resolve():
    selected = {}
    for iteration in range(40):
        constraints = {}
        pending = list(map(Requirement, DIRECT))
        expanded = set()
        while pending:
            requirement = pending.pop(0)
            if requirement.marker and not requirement.marker.evaluate(ENV): continue
            name = canonical(requirement.name)
            constraints.setdefault(name, []).append(requirement)
            if name in expanded: continue
            expanded.add(name)
            previous = selected.get(name)
            if previous:
                pending.extend(Requirement(r) for r in metadata(name, previous)['info'].get('requires_dist') or [])
        replacement = {}
        for name, requirements in sorted(constraints.items()):
            choices = metadata(name)['releases']
            versions = []
            for value in choices:
                try: version = Version(value)
                except InvalidVersion: continue
                if not version.is_prerelease and all(version in r.specifier for r in requirements): versions.append(version)
            for version in sorted(versions, reverse=True):
                value = metadata(name, str(version))
                py = value['info'].get('requires_python')
                from packaging.specifiers import SpecifierSet
                if (not py or Version('3.12.10') in SpecifierSet(py)) and windows_wheels(value):
                    replacement[name] = str(version)
                    break
            else: raise ValueError('No Windows wheel for ' + name + ': ' + str(requirements))
        if replacement == selected: return selected
        selected = replacement
    raise ValueError('Dependency constraints did not converge')


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    selected = resolve()
    lines = ['# Budget3D inference: Windows CPython 3.12. Resolved from metadata; no wheel or model executed.']
    for name, version in selected.items():
        if name in ('torch', 'torchvision'):
            page = read('https://download.pytorch.org/whl/cu128/' + name + '/').decode()
            links = [html.unescape(value) for value in re.findall(r'href="([^"]+)"', page)]
            from urllib.parse import unquote
            matches = [url for url in links if f'{name}-{version}+cu128-cp312-cp312-win_amd64.whl#sha256=' in unquote(url)]
            if len(matches) != 1: raise ValueError('Missing uniquely pinned CUDA wheel for ' + name)
            url, digest = matches[0].split('#sha256=')
            lines.append(f'{name} @ {url} --hash=sha256:{digest}')
        else:
            digests = sorted({row['digests']['sha256'] for row in windows_wheels(metadata(name, version))})
            lines.append(f'{name}=={version} ' + ' '.join('--hash=sha256:' + value for value in digests))
    TARGET.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'packages': len(selected), 'lock': str(TARGET)}))


if __name__ == '__main__': main()
