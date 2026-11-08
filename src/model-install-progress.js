// Shared installation progress for source and recovered desktop/phone views.
export function createDownloadRateTracker() {
  const jobs = new Map(), unknown = { bytesPerSecond: null, remainingSeconds: null };
  return { observe(progress, now = Date.now()) {
    const key = JSON.stringify([progress?.modelId, progress?.operationId || progress?.jobId || 'legacy']);
    const bytes = Number(progress?.downloadedBytes), total = Number(progress?.totalBytes);
    if (progress?.phase !== 'downloading' || !Number.isFinite(bytes) || !Number.isFinite(total) || total <= 0) {
      jobs.delete(key); return unknown;
    }
    let job = jobs.get(key);
    if (!job || bytes < job.bytes || now < job.at || now - job.changedAt > 10000) {
      job = { bytes, at: now, changedAt: now, samples: [{ bytes, at: now }] };
      jobs.set(key, job);
      if (jobs.size > 32) jobs.delete(jobs.keys().next().value);
      return unknown;
    }
    if (bytes > job.bytes) {
      job.bytes = bytes; job.changedAt = now;
      job.samples.push({ bytes, at: now });
      while (job.samples.length > 2 && now - job.samples[1].at > 30000) job.samples.shift();
      while (job.samples.length > 32) job.samples.shift();
    }
    job.at = now;
    const first = job.samples[0], last = job.samples.at(-1), elapsed = last.at - first.at;
    if (elapsed < 2000 || last.bytes <= first.bytes || now - job.changedAt > 10000) return unknown;
    const bytesPerSecond = (last.bytes - first.bytes) * 1000 / elapsed;
    return { bytesPerSecond, remainingSeconds: Math.ceil(Math.max(0, total - bytes) / bytesPerSecond) };
  } };
}

export function createModelInstallProgress(React) {
  const h = React.createElement, meter = createDownloadRateTracker();
  const gb = bytes => (Math.max(0, Number(bytes) || 0) / 1e9).toFixed(3) + ' GB';
  const duration = seconds => seconds < 60 ? Math.max(1, seconds) + ' seconds' : seconds < 3600 ? Math.ceil(seconds / 60) + ' minutes' : (seconds / 3600).toFixed(1) + ' hours';
  const phases = { preparing: 'Preparing', downloading: 'Downloading', verifying: 'Verifying', paused: 'Paused', cancelling: 'Cancelling', ready: 'Complete', failed: 'Failed' };
  return function ModelInstallProgress({ progress, label, busy = false, onPause, onCancel, onDismiss, onError = () => {} }) {
    const [, tick] = React.useState(0), [pending, setPending] = React.useState('');
    const phase = progress?.phase, key = progress?.operationId || progress?.jobId || progress?.modelId;
    React.useEffect(() => {
      if (phase !== 'downloading') return;
      const timer = setInterval(() => tick(value => value + 1), 1000);
      return () => clearInterval(timer);
    }, [key, phase]);
    if (!progress || phase === 'cancelled') return null;
    const measured = meter.observe(progress), active = ['preparing', 'downloading', 'verifying', 'cancelling'].includes(phase);
    const downloading = phase === 'downloading' && progress.totalBytes > 0;
    const action = async (name, callback) => {
      if (busy || pending || !callback) return;
      setPending(name);
      try { await callback(); } catch (error) { onError(String(error)); } finally { setPending(''); }
    };
    return h('div', { className: 'model-install-progress', style: { minWidth: 0, overflowWrap: 'anywhere' }, role: progress.error && phase === 'failed' ? 'alert' : 'status' },
      h('div', null, h('strong', null, label || progress.modelId), h('span', null, phases[phase] || phase)),
      active ? h('progress', { 'aria-label': 'Model download', ...(downloading ? { value: Math.max(0, Math.min(progress.totalBytes, progress.downloadedBytes || 0)), max: progress.totalBytes } : {}) }) : null,
      progress.totalBytes > 0 ? h('small', null, gb(progress.downloadedBytes) + ' / ' + gb(progress.totalBytes)) : null,
      h('small', null, progress.currentFile || (phase === 'verifying' ? 'Verifying downloaded files…' : phase === 'preparing' ? 'Preparing installation…' : phases[phase] || phase)),
      phase === 'downloading' ? h('small', { className: 'model-download-speed' }, measured.bytesPerSecond === null ? 'Measuring download speed…' : (measured.bytesPerSecond < 10000 ? '< 0.01' : (measured.bytesPerSecond / 1e6).toFixed(2)) + ' MB/s') : null,
      phase === 'downloading' ? h('small', null, measured.remainingSeconds === null ? 'Estimating download time…' : 'About ' + duration(measured.remainingSeconds) + ' remaining · measured download rate') : null,
      progress.error && phase === 'failed' ? h('p', null, progress.error) : null,
      active || phase === 'paused' ? h('div', { className: 'model-install-actions', style: { display: 'flex', flexWrap: 'wrap', gap: 8 } },
        active && onPause ? h('button', { type: 'button', style: { minHeight: 44 }, disabled: busy || !!pending || phase === 'cancelling', onClick: () => action('pause', onPause) }, pending === 'pause' ? 'Pausing…' : 'Pause installation') : null,
        onCancel ? h('button', { type: 'button', style: { minHeight: 44 }, disabled: busy || !!pending || phase === 'cancelling', onClick: () => action('cancel', onCancel) }, pending === 'cancel' || phase === 'cancelling' ? 'Cancelling…' : 'Cancel installation') : null) : phase !== 'uninstalling' && onDismiss ? h('button', { type: 'button', style: { minHeight: 44 }, onClick: onDismiss }, 'Dismiss notification') : null);
  };
}
