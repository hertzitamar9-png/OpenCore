import type { GatewaySnapshot, RuntimeSnapshot } from './types';

export function GatewayAvailability({ gateway }: { gateway?: GatewaySnapshot }) {
  const status = gateway?.status;
  const label = status === 'ready' ? 'Gateway ready' : status === 'recovering' ? 'Gateway reconnecting' : status === 'stopped' ? 'Gateway stopped' : 'Gateway checking';
  return <span className="statusbar-state" title={gateway?.error || undefined} role="status"><span className={`status-dot ${status === 'ready' ? 'good' : status === 'stopped' ? 'bad' : 'warn'}`} />{label}</span>;
}
export function RuntimeAvailability({ runtime, gateway }: { runtime: RuntimeSnapshot; gateway?: GatewaySnapshot }) {
  const model = runtime.status === 'running' ? 'Model loaded' : runtime.status === 'starting' ? 'Model loading' : runtime.status === 'error' ? 'Model error' : 'Model unloaded';
  return <><GatewayAvailability gateway={gateway} /><span title={runtime.error || undefined}>{model}</span></>;
}
