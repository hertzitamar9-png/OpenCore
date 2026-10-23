"""Publish a fully verified draft, then expose its signed public update feed."""
import argparse
import json
from pathlib import Path
import subprocess

from package import file_hash

HERE = Path(__file__).resolve().parent
REPOSITORY = 'hertzitamar9-png/OpenCore'


def command(*args):
    return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout


def api(path, allow_missing=False):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode:
        if allow_missing and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError('Could not read GitHub release metadata: ' + result.stderr.strip())
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    folder = args.directory.resolve(strict=True)
    tag = 'app-v' + args.version
    package = json.loads((folder / 'package-manifest.json').read_text(encoding='utf-8'))
    installer_check = json.loads((folder / 'installer-verification.json').read_text(encoding='utf-8'))
    runtime_check = json.loads((folder / 'runtime-verification.json').read_text(encoding='utf-8'))
    if package['build'] != args.version or package['commit'] != args.commit or not installer_check['passed'] or not runtime_check['passed']:
        raise ValueError('A release requires matching completed installer and runtime checks')
    installer = folder / 'OpenCore Setup.exe'
    signature = folder / 'OpenCore Setup.exe.sig'
    subprocess.run(['node', str(HERE / 'verify-signature.cjs'), str(installer), str(signature),
                    str(HERE.parent / 'src-tauri/tauri.conf.json')], check=True)
    release = api(f'repos/{REPOSITORY}/releases/tags/{tag}', allow_missing=True)
    if release and not release['draft']:
        raise ValueError('This release is already public; never replace a published installer')
    if not release:
        command('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', args.commit,
                '--draft', '--latest=false', '--title', 'OpenCore', '--notes-file', str(HERE / 'release-notes.md'))
    initial = [installer, signature, folder / 'package-manifest.json',
               folder / 'installer-verification.json', folder / 'runtime-verification.json']
    command('gh', 'release', 'upload', tag, '--repo', REPOSITORY, '--clobber', *map(str, initial))
    release = api(f'repos/{REPOSITORY}/releases/tags/{tag}')
    metadata = folder / 'release-metadata.json'
    metadata.write_text(json.dumps(release), encoding='utf-8')
    subprocess.run(['node', str(HERE / 'write-update-manifest.cjs'), str(folder), str(metadata), args.version], check=True)
    remote_installer = next(asset for asset in release['assets'] if asset['name'] in
                            {'OpenCore Setup.exe', 'OpenCore.Setup.exe', 'OpenCore-Setup.exe'})
    digest = file_hash(installer)
    if remote_installer.get('digest') != 'sha256:' + digest:
        raise ValueError('GitHub did not confirm the verified installer hash')
    (folder / 'SHA256SUMS.txt').write_text(digest + '  ' + remote_installer['name'] + '\n', encoding='ascii')
    command('gh', 'release', 'upload', tag, '--repo', REPOSITORY, '--clobber',
            str(folder / 'latest.json'), str(folder / 'SHA256SUMS.txt'))
    latest = api(f'repos/{REPOSITORY}/releases/latest', allow_missing=True)
    latest_version = tuple(map(int, latest['tag_name'].removeprefix('app-v').split('.'))) if latest else (0,)
    make_latest = tuple(map(int, args.version.split('.'))) > latest_version
    command('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--prerelease=false',
            '--latest=' + str(make_latest).lower())
    published = api(f'repos/{REPOSITORY}/releases/tags/{tag}')
    if published['draft'] or not any(asset['name'] == 'latest.json' for asset in published['assets']):
        raise RuntimeError('The completed signed update was not published')
    print(json.dumps({'published': True, 'version': args.version, 'commit': args.commit,
                      'installerSha256': digest, 'url': published['html_url'],
                      'installerUrl': remote_installer['browser_download_url']}))


if __name__ == '__main__':
    main()
