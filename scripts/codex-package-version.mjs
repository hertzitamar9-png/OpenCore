export function expectedPlatformPackageVersion(baseVersion, packageName) {
  const prefix = 'codex-';
  if (typeof packageName !== 'string' || !packageName.startsWith(prefix) || packageName.length === prefix.length) {
    throw new TypeError('Expected a Codex platform package with an OS and architecture suffix');
  }
  return `${baseVersion}-${packageName.slice(prefix.length)}`;
}
