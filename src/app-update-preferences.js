const storageKey = 'opencore.update-preferences';
const defaultPreferences = { mode: 'manual', delaySeconds: 30 };
const minDelay = 5;
const maxDelay = 3600;

function validDelay(value) {
  return Number.isInteger(value) && value >= minDelay && value <= maxDelay;
}

function supportsAutomaticUpdates() {
  return typeof window !== 'undefined'
    && '__TAURI_INTERNALS__' in window
    && globalThis.__OPENCORE_MOBILE__ !== true;
}

export function createAppUpdatePreferences(React) {
  const h = React.createElement;
  const listeners = new Set();
  let preferences = defaultPreferences;
  let attemptedVersion = null;
  try {
    const saved = JSON.parse(window.localStorage.getItem(storageKey));
    if (saved?.mode === 'automatic' || saved?.mode === 'manual') {
      preferences = { mode: saved.mode, delaySeconds: validDelay(saved.delaySeconds) ? saved.delaySeconds : defaultPreferences.delaySeconds };
    }
  } catch {
    // A fresh profile or unavailable storage starts in manual mode.
  }
  const subscribe = listener => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  };
  const snapshot = () => preferences;
  const lastAttempt = () => attemptedVersion;
  const usePreferences = () => React.useSyncExternalStore(subscribe, snapshot, () => defaultPreferences);
  function markUpdateAttempt(version) {
    if (!version || version === attemptedVersion) return;
    attemptedVersion = version;
    listeners.forEach(listener => listener());
  }
  function save(patch) {
    const next = { ...preferences, ...patch };
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      return false;
    }
    preferences = next;
    listeners.forEach(listener => listener());
    return true;
  }

  function UpdatePreferences() {
    const { mode, delaySeconds } = usePreferences();
    const [error, setError] = React.useState('');
    if (!supportsAutomaticUpdates()) return null;
    function persist(patch) {
      setError(save(patch) ? '' : 'Could not save your update preference.');
    }
    return h('fieldset', { className: 'app-update-preferences' },
      h('legend', null, 'Update mode'),
      h('label', null, h('input', {
        type: 'radio', name: 'opencore-update-mode', checked: mode === 'manual',
        onChange: () => persist({ mode: 'manual' }),
      }), 'Manual'),
      h('label', null, h('input', {
        type: 'radio', name: 'opencore-update-mode', checked: mode === 'automatic',
        onChange: () => persist({ mode: 'automatic' }),
      }), 'Automatic'),
      mode === 'automatic' && h('label', { className: 'app-update-delay' }, 'Delay before installing (seconds)',
        h('input', {
          key: delaySeconds, type: 'number', defaultValue: delaySeconds, min: minDelay, max: maxDelay, step: 1,
          onBlur: event => {
            const value = event.currentTarget.valueAsNumber;
            if (validDelay(value)) persist({ delaySeconds: value });
            else event.currentTarget.value = String(delaySeconds);
          },
          onKeyDown: event => { if (event.key === 'Enter') event.currentTarget.blur(); },
        }),
      ),
      h('small', null, mode === 'automatic'
        ? 'Installs after the countdown when OpenCore is idle, then reopens the app.'
        : 'Install when you choose Update ready.'),
      error && h('small', { role: 'alert' }, error),
    );
  }

  function useUpdateSchedule({ version, blocked, busy, install }) {
    const { mode, delaySeconds } = usePreferences();
    const attempted = React.useSyncExternalStore(subscribe, lastAttempt, () => null);
    const [seconds, setSeconds] = React.useState(null);
    const current = React.useRef(null);
    current.current = { blocked, busy, install, version };
    const armed = supportsAutomaticUpdates() && mode === 'automatic' && !!version && attempted !== version;
    React.useEffect(() => {
      if (!armed || blocked) {
        setSeconds(null);
        return;
      }
      const deadline = Date.now() + delaySeconds * 1000;
      setSeconds(delaySeconds);
      const timer = window.setInterval(() => {
        const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        setSeconds(remaining);
        const latest = current.current;
        const editor = document.activeElement;
        const draftOpen = editor?.matches('textarea, [contenteditable="true"]')
          && (editor.value || editor.textContent || '').trim();
        if (remaining || latest.busy || latest.blocked || draftOpen || latest.version !== version || attemptedVersion === version
          || preferences.mode !== 'automatic' || preferences.delaySeconds !== delaySeconds) return;
        window.clearInterval(timer);
        markUpdateAttempt(version);
        setSeconds(null);
        void latest.install();
      }, 1000);
      return () => window.clearInterval(timer);
    }, [armed, blocked, delaySeconds, version]);
    if (!armed) return '';
    if (blocked) return 'Update waits for current work to finish';
    if (seconds === null) return '';
    return seconds > 0 ? 'Automatic update in ' + seconds + 's' : 'Update waits for OpenCore to be idle';
  }
  return { UpdatePreferences, markUpdateAttempt, useUpdateSchedule };
}
