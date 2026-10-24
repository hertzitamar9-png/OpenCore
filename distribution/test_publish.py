"""Release regression checks: draft-only lookup, retries and verified public URLs."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import publish


class DraftLookupTests(unittest.TestCase):
    def test_creation_reads_the_json_response_without_shell_quoting(self):
        payload = {'tag_name': 'app-v0.3.145', 'draft': True, 'body': 'Two lines\nwith "quotes" and $()'}
        with patch.object(publish.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, stdout='{"id":145,"draft":true}', stderr='')) as run:
            self.assertEqual(publish.api('repos/example/app/releases', data=payload)['id'], 145)
        self.assertIn('--input', run.call_args.args[0])
        self.assertEqual(json.loads(run.call_args.kwargs['input']), payload)

    def test_draft_absent_from_tag_endpoint_is_found_in_paginated_releases(self):
        draft = {'id': 145, 'tag_name': 'app-v0.3.145', 'draft': True}
        with patch.object(publish, 'api', side_effect=[None, [{'tag_name': 'older'}] * 100, [draft]]) as api:
            self.assertEqual(publish.find_release('app-v0.3.145'), draft)
        self.assertTrue(api.call_args_list[-1].args[0].endswith('per_page=100&page=2'))

    def test_published_tag_needs_no_draft_lookup(self):
        release = {'id': 145, 'tag_name': 'app-v0.3.145', 'draft': False}
        with patch.object(publish, 'api', return_value=release) as api:
            self.assertEqual(publish.find_release('app-v0.3.145'), release)
            self.assertEqual(api.call_count, 1)

    def test_missing_release_returns_none(self):
        with patch.object(publish, 'api', side_effect=[None, []]):
            self.assertIsNone(publish.find_release('app-v0.3.145'))


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.version = '0.3.145'
        self.commit = 'a' * 40
        (self.folder / 'OpenCore Package.exe').write_bytes(b'verified-installer-fixture')
        (self.folder / 'OpenCore Package.exe.sig').write_text('signature-fixture', encoding='utf-8')
        (self.folder / 'OpenCore Setup.exe').write_bytes(b'verified-bootstrap-fixture')
        (self.folder / 'OpenCore Setup.exe.sig').write_text('bootstrap-signature-fixture', encoding='utf-8')
        (self.folder / 'bootstrap-verification.json').write_text(json.dumps({
            'passed': True, 'version': self.version, 'sha256': publish.file_hash(self.folder / 'OpenCore Setup.exe'),
            'signatureVerificationPassed': True, 'tamperedSignatureRejected': True, 'tamperedHashRejected': True}))
        for name, value in {
            'package-manifest.json': {'build': self.version, 'commit': self.commit, 'appExeSha256': 'app-hash'},
            'installer-verification.json': {'passed': True, 'build': self.version, 'exactAppSha256': 'app-hash'},
            'runtime-verification.json': {'passed': True},
        }.items():
            (self.folder / name).write_text(json.dumps(value), encoding='utf-8')
        self.release = {'id': 145, 'tag_name': 'app-v' + self.version, 'target_commitish': self.commit,
                        'draft': True, 'html_url': 'temporary-draft-url', 'assets': []}
        for path in self.folder.iterdir():
            self.add_asset(path)
        self.commands = []
        self.real_run = subprocess.run

    def add_asset(self, path):
        name = path.name.replace(' ', '.')
        self.release['assets'] = [asset for asset in self.release['assets'] if asset['name'] != name]
        self.release['assets'].append({'name': name, 'state': 'uploaded', 'size': path.stat().st_size,
            'digest': 'sha256:' + publish.file_hash(path),
            'browser_download_url': f'https://github.com/{publish.REPOSITORY}/releases/download/untagged-abc123/{name}'})

    def api(self, endpoint, allow_missing=False, data=None):
        if '/releases/tags/' in endpoint:
            return None  # The real GitHub failure: no Git tag exists while the release is a draft.
        if '/releases?' in endpoint:
            return [copy.deepcopy(self.release)]
        if endpoint.endswith('/releases/145'):
            return copy.deepcopy(self.release)
        if endpoint.endswith('/releases/latest'):
            return {'tag_name': 'app-v0.2.142'}
        self.fail('Unexpected endpoint: ' + endpoint)

    def command(self, *args):
        self.commands.append(args)
        if args[2] == 'upload':
            for path in args[7:]:
                self.add_asset(Path(path))
        elif args[2] == 'edit':
            self.release['draft'] = False
            self.release['html_url'] = f'https://github.com/{publish.REPOSITORY}/releases/tag/app-v{self.version}'
            for asset in self.release['assets']:
                asset['browser_download_url'] = asset['browser_download_url'].replace('untagged-abc123', 'app-v' + self.version)
        else:
            self.fail('Unexpected release mutation: ' + repr(args))
        return ''

    def run_tool(self, args, **kwargs):
        if str(args[1]).endswith('verify-signature.cjs'):
            return subprocess.CompletedProcess(args, 0)  # Signature cryptography has separate real-artifact verification.
        return self.real_run(args, **kwargs)

    def run_publication(self):
        with patch.object(publish, 'api', side_effect=self.api), \
             patch.object(publish, 'command', side_effect=self.command), \
             patch.object(publish.subprocess, 'run', side_effect=self.run_tool), redirect_stdout(io.StringIO()):
            publish.publish(self.folder, self.version, self.commit)

    def test_resume_publishes_verified_draft_without_reuploading_installer(self):
        self.run_publication()
        self.assertFalse(self.release['draft'])
        uploads = [command for command in self.commands if command[2] == 'upload']
        self.assertEqual(len(uploads), 1)
        self.assertEqual({Path(name).name for name in uploads[0][7:]}, {'latest.json', 'SHA256SUMS.txt'})
        manifest = json.loads((self.folder / 'latest.json').read_text())
        self.assertEqual(manifest['platforms']['windows-x86_64']['url'],
                         f'https://github.com/{publish.REPOSITORY}/releases/download/app-v0.3.145/OpenCore.Package.exe')

    def test_first_publication_creates_a_draft_then_refreshes_it_by_id(self):
        self.release['assets'] = []
        created = False
        api = self.api
        command = self.command

        def initially_missing(endpoint, allow_missing=False, data=None):
            if data is not None:
                nonlocal created
                self.assertTrue(endpoint.endswith('/releases'))
                self.assertEqual(data['target_commitish'], self.commit)
                self.assertTrue(data['draft'])
                created = True
                return copy.deepcopy(self.release)
            if '/releases/tags/' in endpoint:
                return None
            if '/releases?' in endpoint:
                self.assertFalse(created, 'Do not re-read a stale release listing after creation')
                return []
            return api(endpoint, allow_missing)

        self.api = initially_missing
        self.run_publication()
        self.assertFalse(self.release['draft'])
        self.assertTrue(created)
        self.assertTrue(any(command[2] == 'upload' and any(
            Path(argument).resolve() == (self.folder / 'OpenCore Package.exe').resolve() for argument in command[7:])
            for command in self.commands))

    def test_existing_public_installer_cannot_be_replaced(self):
        self.release['draft'] = False
        with self.assertRaisesRegex(ValueError, 'already public'):
            self.run_publication()
        self.assertFalse(self.commands)

    def test_draft_from_another_commit_cannot_be_published(self):
        self.release['target_commitish'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.run_publication()
        self.assertFalse(self.commands)

    def test_report_from_another_installer_build_cannot_be_published(self):
        report = self.folder / 'installer-verification.json'
        value = json.loads(report.read_text()); value['build'] = '0.3.144'
        report.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'matching completed'):
            self.run_publication()
        self.assertFalse(self.commands)

    def test_unconfirmed_remote_installer_hash_blocks_publication(self):
        installer = next(asset for asset in self.release['assets'] if asset['name'] == 'OpenCore.Package.exe')
        installer['digest'] = 'sha256:wrong'
        self.command = lambda *args: self.commands.append(args)  # Upload never becomes verified on the server.
        with self.assertRaisesRegex(ValueError, 'asset hashes'):
            self.run_publication()
        self.assertTrue(self.release['draft'])
        self.assertFalse(any(command[2] == 'edit' for command in self.commands))

    def test_setup_size_limit_blocks_publication(self):
        setup = self.folder / 'OpenCore Setup.exe'
        with setup.open('wb') as output:
            output.truncate(2_000_001)
        with self.assertRaisesRegex(ValueError, 'small setup'):
            self.run_publication()
        self.assertFalse(self.commands)


if __name__ == '__main__':
    unittest.main()
