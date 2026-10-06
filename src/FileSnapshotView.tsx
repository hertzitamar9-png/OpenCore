import { useEffect, useState } from 'react';
import { FileCode2, ShieldCheck } from 'lucide-react';
import { CaptureCoverage, FileLineCounts } from './FileChangesReceipt';
import { fileName, fileSize, workspaceFiles, type FileDiffResult, type FilePreviewResult, type FileRecord } from './workspaces';
import './workspaces.css';

type View = 'before' | 'after' | 'diff';
const maxDisplayCharacters = 250_000;
function SnapshotText({ text }: { text: string }) {
  return <><pre className="file-snapshot-text">{text.slice(0, maxDisplayCharacters)}</pre>{text.length > maxDisplayCharacters && <p className="workspace-muted">Preview shows the first {maxDisplayCharacters.toLocaleString()} characters. The complete snapshot is saved.</p>}</>;
}

export function FileSnapshotView({ file, onNotice }: { file: FileRecord; onNotice?: (message: string) => void }) {
  const initial: View = file.change === 'deleted' ? 'before' : 'after';
  const [selection, setSelection] = useState<{ id: string; view: View }>({ id: file.id, view: initial });
  const view = selection.id === file.id ? selection.view : initial;
  const [content, setContent] = useState<{ id: string; view: View; preview?: FilePreviewResult; diff?: FileDiffResult } | null>(null);
  const [error, setError] = useState('');
  const [source, setSource] = useState(false);
  useEffect(() => { setSource(false); }, [file.id]);
  useEffect(() => {
    let alive = true;
    setContent(null); setError('');
    async function load() {
      try {
        if (view === 'diff') {
          const diff = await workspaceFiles({ action: 'diff', id: file.id });
          if (alive) setContent({ id: file.id, view, diff });
        } else {
          const hash = view === 'before' ? file.beforeHash : file.afterHash;
          if (!hash) { if (alive) setError(`The ${view} version is unavailable for this record.`); return; }
          const preview = await workspaceFiles({ action: 'preview', id: file.id, version: view });
          if (alive) setContent({ id: file.id, view, preview });
        }
      } catch (cause) { if (alive) setError(String(cause)); }
    }
    void load();
    return () => { alive = false; };
  }, [file.id, file.beforeHash, file.afterHash, view]);
  const current = content?.id === file.id && content.view === view ? content : null;
  const preview = current?.preview;
  const diff = current?.diff;
  return <section className="file-snapshot-view" aria-label={`File ${file.path}`}>
    <header className="file-snapshot-heading"><FileCode2 size={19} /><div><h3>{fileName(file.path)}</h3><p title={file.source}>{file.path}</p></div></header>
    <div className="file-snapshot-meta"><span>{file.change}</span><span>{fileSize(preview?.size ?? file.size)}</span><span>{file.mime}</span></div>
    <div className="file-snapshot-tabs" aria-label="File versions">
      <button aria-pressed={view === 'after'} disabled={!file.afterHash} onClick={() => setSelection({ id: file.id, view: 'after' })}>After</button>
      <button aria-pressed={view === 'before'} disabled={!file.beforeHash} onClick={() => setSelection({ id: file.id, view: 'before' })}>Before</button>
      <button aria-pressed={view === 'diff'} onClick={() => setSelection({ id: file.id, view: 'diff' })}>Diff</button>
      {preview?.mime === 'text/html' && <button className="file-source-toggle" aria-pressed={source} onClick={() => setSource(value => !value)}>{source ? 'Preview' : 'Source'}</button>}
    </div>
    {error ? <p role="alert" className="workspace-inline-error">{error}{onNotice && <button onClick={() => onNotice(error)}>Show notice</button>}</p> : !current ? <p className="workspace-muted" role="status">Loading saved file…</p> : null}
    {preview && <div className="file-snapshot-content">
      {preview.mime === 'text/html' && preview.text !== undefined && !source ? <iframe title={`Preview of ${preview.name}`} srcDoc={preview.text} sandbox="" referrerPolicy="no-referrer" />
        : preview.dataUrl && preview.mime.startsWith('image/') ? <img src={preview.dataUrl} alt={preview.name} />
        : preview.dataUrl && preview.mime.startsWith('audio/') ? <audio controls src={preview.dataUrl} />
        : preview.dataUrl && preview.mime.startsWith('video/') ? <video controls src={preview.dataUrl} />
        : preview.text !== undefined ? <SnapshotText text={preview.text} />
        : <p className="workspace-muted">This binary format has no inline preview.</p>}
      <footer className="file-snapshot-proof"><ShieldCheck size={14} /><span>{preview.snapshotAvailable === false ? 'Verified source reference' : 'Verified snapshot'}</span><code title={preview.sha256}>{preview.sha256.slice(0, 12)}</code></footer>
    </div>}
    {diff && <div className="file-diff-view"><header><span>Changes</span><FileLineCounts added={diff.added} removed={diff.removed} labelled /></header>
      {diff.binary ? <p className="workspace-muted">Binary file. Select a saved version to preview its contents.</p> : <div className="file-diff-columns">
        <section><h4>Before</h4>{diff.before === null || diff.before === undefined ? <p className="workspace-muted">No file</p> : <SnapshotText text={diff.before} />}</section>
        <section><h4>After</h4>{diff.after === null || diff.after === undefined ? <p className="workspace-muted">No file</p> : <SnapshotText text={diff.after} />}</section>
      </div>}<CaptureCoverage coverage={diff.coverage || []} />
    </div>}
    <details className="file-provenance"><summary>File origin</summary><dl><dt>Source</dt><dd>{file.source}</dd><dt>Chat</dt><dd>{file.conversationId || 'No recorded chat link'}</dd><dt>Task or job</dt><dd>{file.turnId}</dd><dt>Recorded</dt><dd>{file.timestamp}</dd></dl></details>
  </section>;
}
