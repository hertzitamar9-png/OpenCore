import assert from 'node:assert/strict';
import { test } from 'node:test';
import { normalizePublicUpdater } from './normalize-public-updater.mjs';

const url = 'https://github.com/example/app/releases/download/app-v0.2.138/app-setup.exe';
const apiUrl = 'https://api.github.com/repos/example/app/releases/assets/123';
const release = {
  tag_name: 'app-v0.2.138', draft: false,
  html_url: 'https://github.com/example/app/releases/tag/app-v0.2.138',
  assets: [{ url: apiUrl, browser_download_url: url, name: 'app-setup.exe' }],
};
const manifest = {
  version: '0.2.138', notes: 'Release notes', pub_date: '2026-10-07T12:00:00Z',
  platforms: {
    'windows-x86_64': { url: apiUrl, signature: 'signed bytes\n' },
    'windows-x86_64-nsis': { url: apiUrl, signature: 'signed bytes\n' },
  },
};

test('public update URLs use published installer assets and preserve signatures and metadata', () => {
  const normalized = normalizePublicUpdater(manifest, release);
  for (const entry of Object.values(normalized.platforms)) {
    assert.equal(entry.url, url);
    assert.equal(entry.signature, 'signed bytes\n');
  }
  assert.equal(normalized.notes, manifest.notes);
  assert.equal(normalized.pub_date, manifest.pub_date);
  assert.equal(manifest.platforms['windows-x86_64'].url, apiUrl);
  assert.deepEqual(normalizePublicUpdater(normalized, release), normalized);
});

test('an incomplete, mismatched or unpublished release is rejected', () => {
  for (const invalid of [
    { ...release, draft: true },
    { ...release, tag_name: 'app-v0.2.137' },
    { ...release, assets: [] },
    { ...release, assets: [{ ...release.assets[0], browser_download_url: 'https://example.org/app.exe' }] },
  ]) assert.throws(() => normalizePublicUpdater(manifest, invalid));
  assert.throws(() => normalizePublicUpdater({ ...manifest, platforms: {} }, release));
  assert.throws(() => normalizePublicUpdater({ ...manifest, platforms: { win: { url: apiUrl, signature: '' } } }, release));
});
