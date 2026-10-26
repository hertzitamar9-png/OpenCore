function Xv({workspace: Workspace = Gv} = {}) {
  const [account, setAccount] = K.useState(), [failed, setFailed] = K.useState(false);
  const [restarting, setRestarting] = K.useState(false);
  const generation = K.useRef(0), timer = K.useRef(), started = K.useRef(0), failures = K.useRef(0);
  const accept = K.useCallback(next => {
    globalThis.__opencoreModelLibrary.setScope(next.user?.id ? String(next.user.id) : '');
    if (next.user) {
      void globalThis.__opencoreModelLibrary.hydrate();
    }
    setAccount(next); setFailed(false);
  }, []);
  const load = K.useCallback(async (reset = false) => {
    clearTimeout(timer.current);
    if (reset) { started.current = Date.now(); failures.current = 0; setFailed(false); }
    const ticket = ++generation.current;
    try {
      let next = await gi('startup_status');
      if (!next.configured && !next.user) next = await ph();
      if (ticket === generation.current) { failures.current = 0; accept(next); }
    } catch {
      if (ticket !== generation.current) return;
      const elapsed = Date.now() - started.current;
      // Helper startup commonly outlives the first native IPC deadline. Keep
      // opening automatically; offer recovery only after a sustained failure.
      setFailed(elapsed >= 60000);
      const delay = elapsed < 15000 ? 500 : elapsed < 60000 ? 1500 : 5000;
      failures.current++;
      timer.current = setTimeout(() => { void load(); }, delay);
    }
  }, [accept]);
  const signedIn = K.useCallback(next => {
    if (!next.user) return;
    generation.current++; clearTimeout(timer.current);
    if (next.restarting) { setRestarting(true); return; }
    accept(next);
  }, [accept]);
  K.useEffect(() => {
    void load(true);
    let closed = false, unlisten;
    Si('opencore-account-changed', ({payload}) => {
      if (closed) return;
      if (payload.restarting) {
        generation.current++; clearTimeout(timer.current);
        globalThis.__opencoreModelLibrary.setScope('');
        setRestarting(true);
      } else { void load(true); }
    }).then(listener => { if (closed) listener(); else unlisten = listener; }).catch(() => {});
    return () => { closed = true; generation.current++; clearTimeout(timer.current); unlisten?.(); };
  }, [load]);
  if (!restarting && account?.user) return r.jsx(K.Suspense, {
    fallback: r.jsx('div', {className: 'account-entry-loading', role: 'status', children: 'Opening your workspace…'}),
    children: r.jsx(Workspace, {})
  });
  return r.jsxs('div', {className: 'account-entry', children: [r.jsx(Yv, {}),
    r.jsx('main', {className: 'account-entry-stage', 'aria-label': 'OpenCore sign in', children:
      r.jsx('div', {className: 'account-entry-card', children:
        !restarting && account ? r.jsx(Uv, {initialState: account, onSignedIn: signedIn}) :
          r.jsxs('section', {className: 'account-entry-loading', role: 'status', 'aria-live': 'polite', children: [
            r.jsx('img', {src: '/opencore-logo.png', alt: '', width: '48', height: '48'}),
            r.jsx('h1', {children: 'Opening OpenCore'}),
            r.jsx('p', {children: restarting ? 'Opening your account workspace…' : failed ? 'OpenCore is taking longer than expected. Still trying to connect…' : 'Preparing your workspace…'}),
            failed && !restarting ? r.jsx('button', {onClick: () => { void load(true); }, children: 'Try again'}) : null,
            r.jsx(vh, {})
          ]})
      })
    })
  ]});
}
