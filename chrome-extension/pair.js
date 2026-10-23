// Only the app's loopback onboarding document can pass a code to this worker.
// The fragment is never sent to the HTTP server or placed in page text.
(() => {
  if (location.origin !== 'http://127.0.0.1:8814' || location.pathname !== '/pair' || location.search) return;
  const token = new URLSearchParams(location.hash.slice(1)).get('pair') || '';
  history.replaceState(null, '', '/pair');
  function pair() {
    const status = document.getElementById('pair-status');
    const help = document.getElementById('extension-missing');
    if (!status) return;
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(token)) {
      status.textContent = 'Return to OpenCore and select Pair Chrome to start pairing.';
      return;
    }
    status.textContent = 'Connecting Chrome to OpenCore…';
    chrome.runtime.sendMessage({ type: 'pair', token }).then(result => {
      if (result?.ok && result?.connected) {
        status.textContent = 'Chrome is connected to OpenCore. You can close this tab.';
        if (help) help.hidden = true;
      } else {
        status.textContent = `${result?.error || 'Chrome could not connect.'} Return to OpenCore and select Pair Chrome to retry.`;
      }
    }).catch(() => {
      status.textContent = 'Could not reach the OpenCore extension. Reload it in chrome://extensions, then select Pair Chrome in OpenCore again.';
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pair, { once: true });
  else pair();
})();
