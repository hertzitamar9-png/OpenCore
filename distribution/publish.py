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


def find_release(tag):
    release = api(f'repos/{REPOSITORY}/releases/tags/{tag}', allow_missing=True)
    if release:
        return release
    # A draft without a Git tag has an untagged-* URL and is absent from the tag endpoint.
    page = 1
    while True:
        releases = api(f'repos/{REPOSITORY}/releases?per_page=100&page={page}')
        for release in releases:
            if release['tag_name'] == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


def checked_release(release_id, tag, commit):
    release = api(f'repos/{REPOSITORY}/releases/{release_id}')
    if release['tag_name'] != tag or release['target_commitish'] != commit:
        raise ValueError('GitHub release identity does not match the verified build')
    return release


def remote_asset(release, local):
    names = {local.name, local.name.replace(' ', '.')}
    matches = [asset for asset in release['assets'] if asset['name'] in names]
    if len(matches) > 1:
        raise ValueError('The draft contains ambiguous assets for ' + local.name)
    return matches[0] if matches else None


def asset_matches(asset, local):
    return (asset and asset.get('state') == 'uploaded' and asset['size'] == local.stat().st_size
            and asset.get('digest') == 'sha256:' + file_hash(local))


def publish(folder, version, commit):
    folder = folder.resolve(strict=True)
    tag = 'app-v' + version
    package = json.loads((folder / 'package-manifest.json').read_text(encoding='utf-8'))
    installer_check = json.loads((folder / 'installer-verification.json').read_text(encoding='utf-8'))
    runtime_check = json.loads((folder / 'runtime-verification.json').read_text(encoding='utf-8'))
    if (package['build'] != version or package['commit'] != commit or not installer_check['passed']
            or installer_check['build'] != version or installer_check['exactAppSha256'] != package['appExeSha256']
            or not runtime_check['passed']):
        raise ValueError('A release requires matching completed installer and runtime checks')
    installer = folder / 'OpenCore Setup.exe'
    signature = folder / 'OpenCore Setup.exe.sig'
    subprocess.run(['node', str(HERE / 'verify-signature.cjs'), str(installer), str(signature),
                    str(HERE.parent / 'src-tauri/tauri.conf.json')], check=True)
    release = find_release(tag)
    if release and not release['draft']:
        raise ValueError('This release is already public; never replace a published installer')
    if not release:
        command('gh', 'release', 'create', tag, '--repo', REPOSITORY, '--target', commit,
                '--draft', '--latest=false', '--title', 'OpenCore', '--notes-file', str(HERE / 'release-notes.md'))
        release = find_release(tag)
    if not release:
        raise RuntimeError('GitHub did not create the release draft')
    release_id = release['id']
    release = checked_release(release_id, tag, commit)
    if not release['draft']:
        raise ValueError('This release is already public; never replace a published installer')
    initial = [installer, signature, folder / 'package-manifest.json',
               folder / 'installer-verification.json', folder / 'runtime-verification.json']
    pending = [local for local in initial if not asset_matches(remote_asset(release, local), local)]
    if pending:
        command('gh', 'release', 'upload', tag, '--repo', REPOSITORY, '--clobber', *map(str, pending))
    release = checked_release(release_id, tag, commit)
    if any(not asset_matches(remote_asset(release, local), local) for local in initial):
        raise ValueError('GitHub did not confirm all verified release asset hashes')
    metadata = folder / 'release-metadata.json'
    metadata.write_text(json.dumps(release), encoding='utf-8')
    subprocess.run(['node', str(HERE / 'write-update-manifest.cjs'), str(folder), str(metadata), version], check=True)
    remote_installer = remote_asset(release, installer)
    digest = file_hash(installer)
    (folder / 'SHA256SUMS.txt').write_text(digest + '  ' + remote_installer['name'] + '\n', encoding='ascii')
    command('gh', 'release', 'upload', tag, '--repo', REPOSITORY, '--clobber',
            str(folder / 'latest.json'), str(folder / 'SHA256SUMS.txt'))
    release = checked_release(release_id, tag, commit)
    if any(not asset_matches(remote_asset(release, folder / name), folder / name)
           for name in ['latest.json', 'SHA256SUMS.txt']):
        raise ValueError('GitHub did not confirm the update manifest and checksum hashes')
    latest = api(f'repos/{REPOSITORY}/releases/latest', allow_missing=True)
    latest_version = tuple(map(int, latest['tag_name'].removeprefix('app-v').split('.'))) if latest else (0,)
    make_latest = tuple(map(int, version.split('.'))) > latest_version
    command('gh', 'release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--prerelease=false',
            '--latest=' + str(make_latest).lower())
    published = checked_release(release_id, tag, commit)
    if published['draft'] or not any(asset['name'] == 'latest.json' for asset in published['assets']):
        raise RuntimeError('The completed signed update was not published')
    print(json.dumps({'published': True, 'version': version, 'commit': commit,
                      'installerSha256': digest, 'url': published['html_url'],
                      'installerUrl': remote_asset(published, installer)['browser_download_url']}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    publish(args.directory, args.version, args.commit)


if __name__ == '__main__':
    main()
