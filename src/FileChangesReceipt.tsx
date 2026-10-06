import { useEffect, useRef, useState } from 'react';
import { FileCode2, Files } from 'lucide-react';
import { subscribeWorkspaceFiles, workspaceFiles, type FileChangesResult, type FileRecord } from './workspaces';
import './workspaces.css';

export function FileLineCounts({ added, removed, labelled = false }: { added: number | null; removed: number | null; labelled?: boolean }) {
  return <span className="file-line-counts">
    {added !== null && <span className="file-lines-added" aria-label={labelled ? `${added} lines added` : undefined}>+{added}</span>}
    {removed !== null && <span className="file-lines-removed" aria-label={labelled ? `${removed} lines removed` : undefined}>−{removed}</span>}
  </span>;
}

export function CaptureCoverage({ coverage }: { coverage: string[] }) {
  return coverage.length ? <details className="workspace-coverage"><summary>Capture coverage · {coverage.length} {coverage.length === 1 ? 'note' : 'notes'}</summary><ul>{coverage.map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul></details> : null;
}

export function FileChangesReceipt({ conversationId, active, onOpen }: { conversationId: string; active: boolean; onOpen: (file: FileRecord) => void }) {
  const [receipt, setReceipt] = useState<FileChangesResult | null>(null);
  const [error, setError] = useState('');
  const previous = useRef<{ conversationId: string; turnId: string | null }>({ conversationId, turnId: null });
  const hiddenTurn = useRef<string | null>(null);
  useEffect(() => {
    let alive = true;
    let revision = 0;
    if (previous.current.conversationId !== conversationId) {
      previous.current = { conversationId, turnId: null };
      hiddenTurn.current = null;
    }
    setReceipt(null); setError('');
    if (active) { hiddenTurn.current = previous.current.turnId; return () => { alive = false; }; }
    async function refresh() {
      const request = ++revision;
      try {
        const value = await workspaceFiles({ action: 'changes', conversationId });
        if (!alive || request !== revision) return;
        if (hiddenTurn.current && value.turnId === hiddenTurn.current) { setReceipt(null); return; }
        previous.current = { conversationId, turnId: value.turnId };
        hiddenTurn.current = null;
        setReceipt(value); setError('');
      } catch (cause) { if (alive && request === revision) { setReceipt(null); setError(String(cause)); } }
    }
    void refresh();
    const unsubscribe = subscribeWorkspaceFiles(() => void refresh());
    return () => { alive = false; unsubscribe(); };
  }, [conversationId, active]);
  if (active) return null;
  if (error) return <p className="workspace-inline-error" role="alert">File history: {error}</p>;
  if (!receipt?.files.length) return receipt?.coverage.length ? <CaptureCoverage coverage={receipt.coverage} /> : null;
  const partial = ['failed', 'cancelled', 'interrupted', 'error'].includes(receipt.status || '');
  const hasLines = receipt.files.some(file => file.added !== null || file.removed !== null);
  return <section className="file-changes-receipt" aria-label="Task file changes">
    <header><span><Files size={15} />{partial ? 'Partial changes' : 'File changes'} <small>{receipt.files.length}</small></span>{hasLines && <FileLineCounts added={receipt.added} removed={receipt.removed} labelled />}</header>
    <div className="file-change-list">{receipt.files.map(file => <button key={file.id} onClick={() => onOpen(file)} title={file.source}>
      <FileCode2 size={15} /><span className="file-change-path">{file.path}</span><small>{file.change}</small>
      {file.added === null && file.removed === null ? <span className="file-binary-label">{/^(text\/|application\/(json|javascript|xml))/.test(file.mime) ? 'Line count unavailable' : 'Binary output'}</span> : <FileLineCounts added={file.added} removed={file.removed} />}
    </button>)}</div>
    <CaptureCoverage coverage={receipt.coverage} />
  </section>;
}
