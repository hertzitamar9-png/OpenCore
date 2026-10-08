import { ThemedSelect } from "./ThemedSelect";
import { useCallback, useEffect, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { Clock3, Copy, FileText, Pause, Play, Plus, RefreshCw, Square, Terminal, Trash2, X } from 'lucide-react';
import { backgroundCommand, backgroundError, describeSchedule, type BackgroundAction, type BackgroundLogs, type BackgroundRun, type BackgroundSchedule, type BackgroundSnapshot, type BackgroundTask } from './background-jobs';
import './BackgroundJobs.css';
import { BackgroundAgentSettings } from './BackgroundAgentSettings';

interface Draft {
  id?: string; name: string; conversationId: string; action: 'prompt' | 'worker'; prompt: string;
  schedule: BackgroundSchedule['kind']; at: string; everySeconds: string; startAt: string; cron: string; timezone: 'utc' | 'local';
  eventName: string; filters: string; stepModulo: string; stepField: string;
  command: string; args: string; cwd: string; usesGpu: boolean; longRunning: boolean; waitPolicy: 'when-idle' | 'allow-during-chat';
  savedPolicy?: string; savedModel?: string;
}
function localInput(value: string | number): string {
  const date = new Date(value); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function makeDraft(conversationId = '', task?: BackgroundTask): Draft {
  const schedule = task?.schedule; const worker = task?.taskAction.kind === 'worker' ? task.taskAction.worker : undefined;
  return { id: task?.id, name: task?.name ?? '', conversationId: task?.conversationId ?? conversationId, action: task?.taskAction.kind ?? 'prompt',
    prompt: task?.taskAction.kind === 'prompt' ? task.taskAction.prompt : '', schedule: schedule?.kind ?? 'once',
    at: schedule?.kind === 'once' ? localInput(schedule.at) : localInput(Date.now() + 60000), everySeconds: schedule?.kind === 'interval' ? String(schedule.everySeconds) : '600',
    startAt: schedule?.kind === 'interval' && schedule.startAt ? localInput(schedule.startAt) : '', cron: schedule?.kind === 'cron' ? schedule.expression : '0 9 * * *',
    timezone: schedule?.kind === 'cron' ? schedule.timezone : 'utc', eventName: schedule?.kind === 'event' ? schedule.name : 'training.checkpoint',
    filters: JSON.stringify(schedule?.kind === 'event' ? schedule.filters ?? {} : {}, null, 2), stepModulo: schedule?.kind === 'event' && schedule.stepModulo ? String(schedule.stepModulo) : '',
    stepField: schedule?.kind === 'event' ? schedule.stepField ?? 'step' : 'step', command: worker?.command ?? '', args: JSON.stringify(worker?.args ?? [], null, 2),
    cwd: worker?.cwd ?? task?.context?.workspace ?? '', usesGpu: worker?.usesGpu ?? false, longRunning: worker?.longRunning ?? false, waitPolicy: worker?.waitPolicy ?? 'when-idle',
    savedPolicy: task?.context?.request.approvalMode, savedModel: task?.context?.modelProfile };
}
function definition(draft: Draft) {
  let schedule: BackgroundSchedule;
  if (draft.schedule === 'once') schedule = { kind: 'once', at: new Date(draft.at).toISOString() };
  else if (draft.schedule === 'interval') {
    const seconds = Number(draft.everySeconds);
    if (!Number.isSafeInteger(seconds) || seconds < 1) throw new Error('Enter a positive interval in seconds.');
    schedule = { kind: 'interval', everySeconds: seconds, ...(draft.startAt ? { startAt: new Date(draft.startAt).toISOString() } : {}) };
  } else if (draft.schedule === 'cron') schedule = { kind: 'cron', expression: draft.cron.trim(), timezone: draft.timezone };
  else {
    const filters: unknown = JSON.parse(draft.filters);
    if (!filters || typeof filters !== 'object' || Array.isArray(filters)) throw new Error('Event filters must be a JSON object.');
    const modulo = draft.stepModulo.trim() ? Number(draft.stepModulo) : null;
    if (modulo !== null && (!Number.isSafeInteger(modulo) || modulo < 1)) throw new Error('Every N steps must be a positive integer.');
    schedule = { kind: 'event', name: draft.eventName.trim(), filters: filters as Record<string, unknown>, ...(modulo === null ? {} : { stepModulo: modulo }), stepField: draft.stepField.trim() || 'step' };
  }
  let taskAction: BackgroundAction;
  if (draft.action === 'prompt') {
    if (!draft.conversationId.trim()) throw new Error('Choose the originating chat to retain its saved model and approval settings.');
    taskAction = { kind: 'prompt', prompt: draft.prompt };
  } else {
    const args: unknown = JSON.parse(draft.args);
    if (!Array.isArray(args) || args.some(argument => typeof argument !== 'string')) throw new Error('Arguments must be a JSON array of strings.');
    taskAction = { kind: 'worker', worker: { command: draft.command.trim(), args: args as string[], cwd: draft.cwd.trim(), usesGpu: draft.usesGpu, longRunning: draft.longRunning, waitPolicy: draft.usesGpu ? 'when-idle' : draft.waitPolicy } };
  }
  return { name: draft.name.trim(), ...(draft.conversationId.trim() ? { conversationId: draft.conversationId.trim() } : {}), schedule, taskAction };
}
const time = (value: string | number | null | undefined) => value == null ? '—' : new Date(value).toLocaleString();
const active = (run: BackgroundRun) => run.status === 'queued' || run.status === 'running';

export function BackgroundJobs({ conversationId, onNotice }: { conversationId?: string; onNotice: (message: string) => void }) {
  const [snapshot, setSnapshot] = useState<BackgroundSnapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [loading, setLoading] = useState(true);
  const [scope, setScope] = useState<'all' | 'chat'>('all');
  const [selectedRun, setSelectedRun] = useState<string | null>(null); const [logs, setLogs] = useState<BackgroundLogs | null>(null);
  const [webhook, setWebhook] = useState<{ url: string; token: string | null } | null>(null);
  const callback = useRef(onNotice); callback.current = onNotice;
  const refresh = useCallback(async () => {
    try { const result = await backgroundCommand({ action: 'list', ...(scope === 'chat' && conversationId ? { conversationId } : {}) }); setSnapshot(result as BackgroundSnapshot); }
    catch (error) { setError(backgroundError(error)); }
    finally { setLoading(false); }
  }, [conversationId, scope]);
  useEffect(() => {
    let mounted = true; let unlisten: (() => void) | undefined;
    void refresh();
    void listen('opencore-background-changed', () => { if (mounted) void refresh(); }).then(stop => { if (mounted) unlisten = stop; else stop(); }).catch(() => {});
    const onFocus = () => void refresh(); window.addEventListener('focus', onFocus);
    return () => { mounted = false; unlisten?.(); window.removeEventListener('focus', onFocus); };
  }, [refresh]);
  const loadLogs = useCallback(async (runId: string) => {
    try { setLogs(await backgroundCommand({ action: 'logs', runId }) as BackgroundLogs); }
    catch (error) { setError(backgroundError(error)); }
  }, []);
  useEffect(() => {
    if (!selectedRun) return;
    void loadLogs(selectedRun);
    if (!snapshot?.runs.some(run => run.id === selectedRun && active(run))) return;
    const timer = window.setInterval(() => void loadLogs(selectedRun), 2000);
    return () => window.clearInterval(timer);
  }, [selectedRun, snapshot, loadLogs]);
  async function perform(action: string, ids: { taskId?: string; runId?: string } = {}) {
    setBusy(`${action}:${ids.taskId ?? ids.runId ?? ''}`); setError('');
    try { await backgroundCommand({ action, ...ids }); await refresh(); }
    catch (error) { const message = backgroundError(error); setError(message); callback.current(message); }
    finally { setBusy(''); }
  }
  async function save() {
    if (!draft) return; setBusy('save'); setError('');
    try {
      const task = definition(draft);
      await backgroundCommand({ action: draft.id ? 'update' : 'create', ...(draft.id ? { taskId: draft.id } : {}), ...(draft.conversationId.trim() ? { conversationId: draft.conversationId.trim() } : {}), task });
      setDraft(null); await refresh(); callback.current(draft.id ? 'Job updated.' : 'Job saved.');
    } catch (error) { setError(backgroundError(error)); }
    finally { setBusy(''); }
  }
  async function setupWebhook() {
    if (webhook) return;
    try { const value = await backgroundCommand({ action: 'webhook_setup' }) as { url: string; token: string }; if (value?.url) setWebhook(value); }
    catch (error) { setError(backgroundError(error)); }
  }
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft(current => current ? { ...current, [key]: value } : current);
  const endpoint = webhook ?? snapshot?.webhook;
  const eventExample = JSON.stringify({ id: 'training-1:checkpoint:500', name: 'training.checkpoint', data: { runId: 'training-1', step: 500, path: 'checkpoints/checkpoint-500' } }, null, 2);
  const shellExample = `Invoke-RestMethod -Method Post -Uri '${endpoint?.url ?? ''}' -Headers @{ Authorization = 'Bearer ${endpoint?.token ?? '<token>'}' } -ContentType 'application/json' -Body '${JSON.stringify(JSON.parse(eventExample))}'`;
  const selected = snapshot?.runs.find(run => run.id === selectedRun);
  return <div className="background-jobs">
    <div className="jobs-toolbar"><div><h2><Clock3 size={19} /> Background work</h2><p>Schedules, event triggers and owned workers.</p></div><div className="jobs-toolbar-actions">
      {conversationId && <ThemedSelect aria-label="Job scope" value={scope} onChange={event => setScope(event.target.value as 'all' | 'chat')}><option value="all">All jobs</option><option value="chat">Current chat</option></ThemedSelect>}
      <button type="button" aria-label="Refresh jobs" disabled={!!busy} onClick={() => void refresh()}><RefreshCw size={15} /></button>
      <button type="button" className="jobs-primary" onClick={() => { setError(''); setDraft(makeDraft(conversationId)); }}><Plus size={15} />New job</button>
    </div></div>
    <BackgroundAgentSettings />
    <div className="jobs-execution"><Clock3 size={16} /><div><strong>Schedules resume automatically while the agent is running.</strong><p>The gateway starts when OpenCore opens. Prompt jobs automatically start their saved model when due, wait for chat, studio and speech work to finish, and save their results in the originating chat. Reopening coalesces missed schedules into one queued run. Interrupted runs retain their evidence and can be retried with Run now. GPU workers keep the GPU until they exit; checkpoint prompts wait in the queue.</p></div></div>
    {error && <div className="jobs-error" role="alert">{error}</div>}
    {draft && <section className="jobs-editor" aria-label={draft.id ? 'Edit job' : 'Create job'}>
      <div className="jobs-section-title"><h3>{draft.id ? 'Edit job' : 'Create job'}</h3><button type="button" aria-label="Close job editor" onClick={() => setDraft(null)}><X size={16} /></button></div>
      <div className="jobs-form-grid">
        <label>Job name<input value={draft.name} onChange={event => set('name', event.target.value)} /></label>
        <label>Action<ThemedSelect value={draft.action} onChange={event => set('action', event.target.value as Draft['action'])}><option value="prompt">Agent prompt</option><option value="worker">Command worker</option></ThemedSelect></label>
        <label className="jobs-wide">Originating chat<input value={draft.conversationId} disabled={!!draft.id} placeholder="Chat ID" onChange={event => set('conversationId', event.target.value)} /></label>
        {draft.action === 'prompt' ? <><label className="jobs-wide">Agent prompt<textarea rows={4} value={draft.prompt} onChange={event => set('prompt', event.target.value)} /></label><p className="jobs-wide jobs-help">When due, OpenCore loads the originating chat’s saved model automatically, runs this instruction with its saved permissions, and saves the result in that chat. Send a message in that chat first to save its settings. It waits while chat, studio or speech work uses the GPU.{draft.savedPolicy && ` Saved approval: ${draft.savedPolicy}. Model: ${draft.savedModel}.`}</p></> : <>
          <label className="jobs-wide">Executable<input value={draft.command} placeholder="python.exe" onChange={event => set('command', event.target.value)} /></label>
          <label className="jobs-wide">Arguments (JSON array)<textarea rows={3} value={draft.args} onChange={event => set('args', event.target.value)} /></label>
          <label className="jobs-wide">Working directory<input value={draft.cwd} placeholder="C:\\project" onChange={event => set('cwd', event.target.value)} /></label>
          <label className="jobs-checkbox"><input type="checkbox" checked={draft.usesGpu} onChange={event => { set('usesGpu', event.target.checked); if (event.target.checked) set('waitPolicy', 'when-idle'); }} />Uses GPU</label>
          <label className="jobs-checkbox"><input type="checkbox" checked={draft.longRunning} onChange={event => set('longRunning', event.target.checked)} />Long running worker</label>
          <label className="jobs-wide">Start policy<ThemedSelect value={draft.waitPolicy} disabled={draft.usesGpu} onChange={event => set('waitPolicy', event.target.value as Draft['waitPolicy'])}><option value="when-idle">Wait for chat, studio and speech to be idle</option><option value="allow-during-chat">Allow CPU work during chat</option></ThemedSelect></label>
          <p className="jobs-wide jobs-help">Exact arguments are passed to the executable. Workers run hidden and stop with OpenCore. Each worker receives OPENCORE_EVENT_URL and OPENCORE_EVENT_TOKEN. GPU workers reserve the GPU until their process exits.</p>
        </>}
        <label className="jobs-wide">Schedule<ThemedSelect value={draft.schedule} onChange={event => set('schedule', event.target.value as Draft['schedule'])}><option value="once">Once</option><option value="interval">Fixed interval</option><option value="cron">Cron</option><option value="event">Named event</option></ThemedSelect></label>
        {draft.schedule === 'once' && <label className="jobs-wide">Run at (local time)<input type="datetime-local" value={draft.at} onChange={event => set('at', event.target.value)} /></label>}
        {draft.schedule === 'interval' && <><label>Every (seconds)<input type="number" min="1" max="31536000" value={draft.everySeconds} onChange={event => set('everySeconds', event.target.value)} /></label><label>First run (optional, local time)<input type="datetime-local" value={draft.startAt} onChange={event => set('startAt', event.target.value)} /></label></>}
        {draft.schedule === 'cron' && <><label>Cron expression<input value={draft.cron} placeholder="0 9 * * *" onChange={event => set('cron', event.target.value)} /></label><label>Timezone<ThemedSelect value={draft.timezone} onChange={event => set('timezone', event.target.value as Draft['timezone'])}><option value="utc">UTC</option><option value="local">Operating system local time</option></ThemedSelect></label><p className="jobs-wide jobs-help">Minute hour day month weekday. Local schedules follow daylight saving changes; skipped wall times are skipped and repeated times run once for each UTC occurrence.</p></>}
        {draft.schedule === 'event' && <><label className="jobs-wide">Event name<input value={draft.eventName} onChange={event => set('eventName', event.target.value)} /></label><label>Every N steps<input type="number" min="1" value={draft.stepModulo} placeholder="Optional, e.g. 500" onChange={event => set('stepModulo', event.target.value)} /></label><label>Step field<input value={draft.stepField} onChange={event => set('stepField', event.target.value)} /></label><label className="jobs-wide">Event filters (JSON object)<textarea rows={3} value={draft.filters} onChange={event => set('filters', event.target.value)} /></label><p className="jobs-wide jobs-help">Filters match structured fields or dotted paths. Use a stable, unique event ID for each checkpoint. Duplicate delivery cannot start another run.</p></>}
      </div><div className="jobs-editor-actions"><button type="button" onClick={() => setDraft(null)}>Cancel</button><button type="button" className="jobs-primary" disabled={!!busy} onClick={() => void save()}>{busy === 'save' ? 'Saving…' : 'Save job'}</button></div>
    </section>}
    <section className="jobs-task-list" aria-labelledby="jobs-tasks-title"><div className="jobs-section-title"><h3 id="jobs-tasks-title">Jobs</h3><span>{snapshot?.tasks.length ?? 0}</span></div>
      {loading ? <p className="jobs-empty">Loading jobs…</p> : !snapshot?.tasks.length ? <p className="jobs-empty">No jobs yet. Create a schedule or event trigger.</p> : snapshot.tasks.map(task => <article className="jobs-task" key={task.id}>
        <div className="jobs-task-heading"><span className={`jobs-status ${task.paused ? 'paused' : 'enabled'}`}>{task.paused ? 'Paused' : 'Enabled'}</span><h4>{task.name}</h4></div>
        <p>{describeSchedule(task.schedule)}</p><p>{task.taskAction.kind === 'prompt' ? 'Runs an agent instruction and automatically loads its saved model when needed.' : 'Runs the program below on this computer. Its exit code and output determine success.'}</p><p className="jobs-action-summary">{task.taskAction.kind === 'prompt' ? task.taskAction.prompt : <><Terminal size={13} />{task.taskAction.worker.command} {task.taskAction.worker.args.join(' ')} · {task.taskAction.worker.cwd}</>}</p>
        <div className="jobs-task-meta"><span>{task.paused ? 'Automatic triggers paused · Run now starts one manual run' : task.nextDue ? `Next: ${time(task.nextDue)}` : task.schedule.kind === 'event' ? 'Waiting for the named event' : 'No future occurrence'}</span>{task.context && <span>Approval: {task.context.request.approvalMode} · {task.context.modelProfile}</span>}{task.taskAction.kind === 'worker' && task.taskAction.worker.usesGpu && <span>GPU reserved until exit</span>}</div>
        <div className="jobs-row-actions"><button type="button" disabled={!!busy} onClick={() => void perform('run_now', { taskId: task.id })}><Play size={13} />Run now</button><button type="button" disabled={!!busy} onClick={() => void perform(task.paused ? 'resume' : 'pause', { taskId: task.id })}>{task.paused ? <Play size={13} /> : <Pause size={13} />}{task.paused ? 'Resume' : 'Pause'}</button><button type="button" disabled={!!busy} onClick={() => { setError(''); setDraft(makeDraft(conversationId, task)); }}>Edit</button><button type="button" disabled={!!busy} onClick={() => void perform('delete', { taskId: task.id })}><Trash2 size={13} />Delete</button></div>
      </article>)}
    </section>
    <section className="jobs-history" aria-labelledby="jobs-runs-title"><div className="jobs-section-title"><h3 id="jobs-runs-title">Run history</h3><span>{snapshot?.runs.length ?? 0}</span></div>
      {!snapshot?.runs.length ? <p className="jobs-empty">Run timestamps, logs and errors appear here.</p> : snapshot.runs.map(run => <article key={run.id} className="jobs-run"><div className="jobs-task-heading"><span className={`jobs-status ${run.status}`}>{run.status}</span><h4>{run.taskName}</h4>{run.exitCode !== null && <span className="jobs-exit">Exit code {run.exitCode}</span>}</div>
        <div className="jobs-run-times"><span>Queued {time(run.queuedAt)}</span>{run.scheduledAt && <span>Scheduled {time(run.scheduledAt)}</span>}<span>Started {time(run.startedAt)}</span><span>Finished {time(run.finishedAt)}</span>{run.pid && <span>PID {run.pid}</span>}</div>
        {run.error && <p className="jobs-run-error">{run.error}</p>}<div className="jobs-row-actions"><button type="button" onClick={() => { setLogs(null); setSelectedRun(run.id); }}><FileText size={13} />Logs</button>{active(run) && <button type="button" disabled={!!busy} onClick={() => void perform('cancel', { runId: run.id })}><Square size={13} />Cancel run</button>}</div>
      </article>)}
    </section>
    {selectedRun && <section className="jobs-logs" aria-label="Run logs"><div className="jobs-section-title"><h3>{selected?.taskName ?? 'Run'} logs</h3><div className="jobs-row-actions"><button type="button" aria-label="Refresh logs" onClick={() => void loadLogs(selectedRun)}><RefreshCw size={14} /></button><button type="button" aria-label="Close logs" onClick={() => { setSelectedRun(null); setLogs(null); }}><X size={14} /></button></div></div><p className="jobs-help">{selectedRun} · {selected?.status ?? ''}. Logs retain up to 32 MiB per stream; this view shows the latest 256 KiB.</p>{logs ? <><h4>stdout{logs.stdoutTruncated ? ' · tail preview' : ''}</h4><pre>{logs.stdout || 'No stdout recorded.'}</pre><h4>stderr{logs.stderrTruncated ? ' · tail preview' : ''}</h4><pre>{logs.stderr || 'No stderr recorded.'}</pre></> : <p>Loading logs…</p>}</section>}
    <details className="jobs-webhook" onToggle={event => { if (event.currentTarget.open) void setupWebhook(); }}><summary>Webhook setup</summary><p>Loopback only. Include the per-install bearer token and a stable event ID. Workers receive the endpoint and token in their environment.</p><code>{endpoint?.url || 'Available in the installed app'}</code><label>Bearer token<input aria-label="Bearer token" readOnly value={endpoint?.token ?? ''} /></label><label>Event example<textarea aria-label="Event example" readOnly rows={9} value={eventExample} /></label><label>PowerShell request<textarea aria-label="PowerShell request" readOnly rows={4} value={shellExample} /></label><button type="button" onClick={() => { void navigator.clipboard?.writeText(shellExample).then(() => callback.current('Event request copied.')).catch(error => setError(backgroundError(error))); }}><Copy size={14} />Copy request</button></details>
  </div>;
}
