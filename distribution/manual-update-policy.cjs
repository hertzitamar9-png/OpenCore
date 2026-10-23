// The distribution can advance while retaining a verified native baseline.
// Compare the offered release with the actual installed distribution, rather
// than repeatedly offering an update against the older native package version.
function createManualUpdatePolicy(installedVersion) {
  function parts(value) {
    if (typeof value !== 'string' || !/^\d+\.\d+\.\d+$/.test(value)) {
      throw new Error('Invalid OpenCore release version');
    }
    const numbers = value.split('.').map(Number);
    if (numbers.some(number => !Number.isSafeInteger(number))) {
      throw new Error('Invalid OpenCore release version');
    }
    return numbers;
  }
  const installed = parts(installedVersion);
  function newer(candidate) {
    const next = parts(candidate);
    for (let i = 0; i < installed.length; i++) {
      if (next[i] !== installed[i]) return next[i] > installed[i];
    }
    return false;
  }
  return {
    currentVersion: installedVersion,
    normalize(result) {
      const available = result?.available === true && typeof result.version === 'string' && newer(result.version);
      return { ...result, currentVersion: installedVersion, available, version: available ? result.version : null };
    },
  };
}
module.exports = { createManualUpdatePolicy };
