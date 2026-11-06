export function createComputerDriverPanel(React, services) {
  const h = React.createElement;
  const serverId = 'opencore-cua-driver';
  return function ComputerDriverPanel() {
    const phone = globalThis.__OPENCORE_PHONE__ === true;
    const [status, setStatus] = React.useState(null), [connected, setConnected] = React.useState(false);
    const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
    const alive = React.useRef(true), connectAfterInstall = React.useRef(false);
    async function refresh() {
      const [next, config] = await Promise.all([services.request('/computer-driver/status'), services.loadConfiguration()]);
      if (alive.current) {
        setStatus(next);
        setConnected(config.mcpServers.some(server => server.id === serverId && server.enabled));
      }
      return next;
    }
    React.useEffect(() => {
      alive.current = true;
      if (phone) { setStatus({supported: false}); return () => { alive.current = false; }; }
      void refresh().catch(reason => {
        if (!alive.current) return;
        if (globalThis.__OPENCORE_MOBILE__) setStatus({supported: false});
        else setError(String(reason));
      });
      return () => { alive.current = false; };
    }, [phone]);
    async function connection(enable) {
      const server = enable ? await services.request('/computer-driver/connection', {}) : null;
      if (!enable) await services.request('/computer-driver/disconnect', {});
      const config = await services.loadConfiguration();
      const servers = config.mcpServers.filter(server => server.id !== serverId);
      if (enable) servers.push(server);
      await services.saveServers(servers);
      if (alive.current) setConnected(enable);
    }
    React.useEffect(() => {
      if (!['downloading', 'preparing'].includes(status?.status)) return;
      let cancelled = false, timer;
      async function poll() {
        try {
          const next = await services.request('/computer-driver/status');
          if (cancelled || !alive.current) return;
          setStatus(next);
          if (next.status === 'installed' && connectAfterInstall.current) {
            connectAfterInstall.current = false;
            setBusy(true);
            try { await connection(true); }
            finally { if (alive.current) setBusy(false); }
          }
          if (['downloading', 'preparing'].includes(next.status)) timer = setTimeout(poll, 700);
        } catch (reason) { if (!cancelled && alive.current) setError(String(reason)); }
      }
      timer = setTimeout(poll, 700);
      return () => { cancelled = true; clearTimeout(timer); };
    }, [status?.status]);
    async function perform(action) {
      if (busy) return;
      setBusy(true); setError('');
      try {
        if (action === 'install') {
          connectAfterInstall.current = true;
          setStatus(await services.request('/computer-driver/install', {}));
        } else if (action === 'cancel') {
          connectAfterInstall.current = false;
          setStatus(await services.request('/computer-driver/cancel', {}));
        } else if (action === 'disconnect') await connection(false);
        else await connection(true);
      } catch (reason) { if (alive.current) setError(String(reason)); }
      finally { if (alive.current) setBusy(false); }
    }
    const working = ['downloading', 'preparing'].includes(status?.status);
    const percent = status?.totalBytes ? Math.min(100, Math.floor(100 * status.downloadedBytes / status.totalBytes)) : 0;
    return h('section', {className: 'computer-driver-panel', 'aria-label': 'Cua Driver'},
      h('div', {className: 'computer-driver-heading'}, h('div', null,
        h('strong', null, 'Cua Driver'), h('small', null, 'Native apps · Accessibility · Window screenshots')),
        h('span', {className: 'computer-driver-badge', role: 'status'}, working ? 'Installing' : connected ? 'Connected to agent' : status?.installed ? 'Installed' : 'Optional')),
      h('p', null, 'Use the upstream open-source driver with your selected model. It follows the allowed apps below and keeps background input on.'),
      status?.supported === false ? h('p', {className: 'appearance-note'}, 'Available on the paired 64-bit Windows PC. Choose Paired PC to configure it from your phone.') :
        h('div', {className: 'computer-driver-actions'},
          connected ? h('button', {type: 'button', disabled: busy, onClick: () => void perform('disconnect')}, 'Disconnect Cua Driver') :
            h('button', {type: 'button', disabled: busy || working || !status,
              onClick: () => void perform(status.installed ? 'connect' : 'install')},
              working ? 'Installing…' : busy ? 'Connecting…' : status?.installed ? 'Connect Cua Driver' : 'Install and connect · 36 MB'),
          working ? h('button', {type: 'button', disabled: busy, onClick: () => void perform('cancel')}, 'Cancel installation') : null,
          status?.installed && !working ? h('button', {type: 'button', disabled: busy, onClick: () => void perform('install')}, 'Repair runtime') : null,
          h('a', {href: 'https://github.com/trycua/cua/tree/cua-driver-rs-v0.40.2/libs/cua-driver', target: '_blank', rel: 'noreferrer'}, 'Project and license')),
      working ? h('div', {className: 'computer-driver-progress'}, h('progress', {value: status.downloadedBytes, max: status.totalBytes, 'aria-label': 'Cua Driver installation progress'}),
        h('small', null, `${status.stage} · ${(status.downloadedBytes / 1e6).toFixed(1)} / ${(status.totalBytes / 1e6).toFixed(1)} MB · ${percent}%`)) : null,
      connected ? h('p', {className: 'appearance-note'}, 'Enable computer use, allow an app below, then ask your model to use it. Actions and results appear in the chat timeline. Disconnect stops this driver and removes it from future agent turns.') : null,
      (error || status?.error) ? h('p', {role: 'alert'}, error || status.error) : null);
  };
}
