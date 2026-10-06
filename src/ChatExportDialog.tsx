import { useEffect, useRef, useState } from 'react';
import { FileDown } from 'lucide-react';
import { FloatingWindow } from './FloatingWindow';

export function ChatExportDialog({onClose, onExport}: {
  onClose: () => void;
  onExport: (format: 'json' | 'markdown') => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const firstButton = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const close = () => { if (!inFlight.current) onClose(); };
  useEffect(() => {
    firstButton.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); if (!inFlight.current) closeRef.current(); }
      if (event.key !== 'Tab') return;
      const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('#chat-export-dialog button:not(:disabled)'));
      const first = buttons[0], last = buttons.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); previousFocus.current?.focus(); };
  }, []);
  async function exportFile(format: 'json' | 'markdown') {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try { await onExport(format); }
    catch (cause) { setError(String(cause)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <><div className="modal-backdrop" role="presentation" onMouseDown={close} />
    <FloatingWindow id="chat-export" domId="chat-export-dialog" title="Export chat" icon={<FileDown size={17} />}
      onClose={close} place="center" modal className="opencore-modal dialog-floating chat-export-dialog"
      initialWidth={460} initialHeight={290} minWidth={330} minHeight={245}>
      <p>Choose a format for the full saved conversation.</p>
      <div className="chat-export-options">
        <button ref={firstButton} disabled={busy} onClick={() => void exportFile('json')}><FileDown size={19} /><span><strong>Export JSON</strong><small>Portable history you can import into OpenCore.</small></span></button>
        <button disabled={busy} onClick={() => void exportFile('markdown')}><FileDown size={19} /><span><strong>Export Markdown</strong><small>A readable copy of messages and activity.</small></span></button>
      </div>
      {busy ? <p role="status">Exporting conversation…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </FloatingWindow>
  </>;
}
