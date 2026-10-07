import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function normalizePublicUpdater(manifest, release) {
  const tag = `app-v${manifest.version}`;
  const page = new URL(release.html_url);
  if (release.draft || release.tag_name !== tag || page.origin !== 'https://github.com'
      || !page.pathname.endsWith(`/releases/tag/${tag}`)) {
    throw new Error('Updater manifest must match its published GitHub release');
  }
  const prefix = `${page.origin}${page.pathname.slice(0, -`/releases/tag/${tag}`.length)}/releases/download/${tag}/`;
  const entries = Object.entries(manifest.platforms ?? {});
  if (!entries.length) throw new Error('Updater manifest has no platforms');
  const platforms = Object.fromEntries(entries.map(([platform, entry]) => {
    const asset = release.assets.find((candidate) => entry.url === candidate.url
      || entry.url === candidate.browser_download_url);
    if (!asset || !asset.browser_download_url.startsWith(prefix)
        || typeof entry.signature !== 'string' || !entry.signature.trim()) {
      throw new Error(`Updater platform ${platform} has no signed asset in this release`);
    }
    return [platform, { ...entry, url: asset.browser_download_url }];
  }));
  return { ...manifest, platforms };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [manifestPath, releasePath] = process.argv.slice(2);
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8').replace(/^\uFEFF/, ''));
    const release = JSON.parse(fs.readFileSync(releasePath, 'utf8').replace(/^\uFEFF/, ''));
    fs.writeFileSync(manifestPath, `${JSON.stringify(normalizePublicUpdater(manifest, release), null, 2)}\n`);
    process.stdout.write(`Public updater manifest ready for ${manifest.version}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
