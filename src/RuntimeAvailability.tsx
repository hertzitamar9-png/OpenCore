import type { GatewaySnapshot, RuntimeSnapshot } from './types';

export function RuntimeAvailability({ runtime, gateway }: { runtime: RuntimeSnapshot; gateway?: GatewaySnapshot }) {
  const status = gateway?.status;
  const label = status === 'ready' ? 'Gateway ready' : status === 'recovering' ? 'Gateway reconnecting' : status === 'stopped' ? 'Gateway stopped' : 'Gateway checking';
  const model = runtime.status === 'running' ? 'Model loaded' : runtime.status === 'starting' ? 'Model loading' : runtime.status === 'error' ? 'Model error' : 'Model unloaded';
  return <><span className="statusbar-state" title={gateway?.error || undefined} role="status"><span className={`status-dot ${status === 'ready' ? 'good' : status === 'stopped' ? 'bad' : 'warn'}`} />{label}</span><span title={runtime.error || undefined}>{model}</span></>;
}
