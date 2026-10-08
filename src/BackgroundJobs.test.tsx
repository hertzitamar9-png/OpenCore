import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { BackgroundJobs } from './BackgroundJobs';
import * as jobs from './background-jobs';

vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn(async () => () => {}) }));
const empty = { tasks: [], runs: [], webhook: { url: 'http://127.0.0.1:4222/background/events', token: 'install-token' }, execution: { appMustBeOpen: true } };
beforeEach(() => { vi.restoreAllMocks(); vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue(empty); });

it('states execution conditions and explains the authenticated loopback event', async () => {
  render(<BackgroundJobs onNotice={vi.fn()} />);
  expect(await screen.findByText(/Schedules resume automatically while the agent is running/)).toBeVisible();
  expect(screen.getByText(/Prompt jobs automatically start their saved model when due/)).toBeVisible();
  fireEvent.click(screen.getByText('Webhook setup'));
  expect(screen.getByText('http://127.0.0.1:4222/background/events')).toBeVisible();
  expect((screen.getByLabelText('Event example') as HTMLTextAreaElement).value).toContain('training.checkpoint');
  expect(screen.getByText(/GPU workers keep the GPU until they exit/)).toBeVisible();
});

it('creates a chat schedule without inventing or upgrading approval settings', async () => {
  const command = vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue(empty);
  render(<BackgroundJobs conversationId="chat-1" onNotice={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New job' }));
  fireEvent.change(screen.getByLabelText('Job name'), { target: { value: 'Review checkpoint' } });
  fireEvent.change(screen.getByLabelText('Agent prompt'), { target: { value: 'Review the new checkpoint logs' } });
  fireEvent.change(screen.getByLabelText('Schedule'), { target: { value: 'interval' } });
  fireEvent.change(screen.getByLabelText('Every (seconds)'), { target: { value: '600' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith({ action: 'create', conversationId: 'chat-1', task: { name: 'Review checkpoint', conversationId: 'chat-1', schedule: { kind: 'interval', everySeconds: 600 }, taskAction: { kind: 'prompt', prompt: 'Review the new checkpoint logs' } } }));
  const create = command.mock.calls.find(([args]) => args.action === 'create')?.[0];
  expect(JSON.stringify(create)).not.toMatch(/allow-all|allow-chat|approvalMode/);
});

it('submits exact worker arguments and event step filters', async () => {
  const command = vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue(empty);
  render(<BackgroundJobs onNotice={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New job' }));
  fireEvent.change(screen.getByLabelText('Job name'), { target: { value: 'Checkpoint analysis' } });
  fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'worker' } });
  fireEvent.change(screen.getByLabelText('Executable'), { target: { value: 'python.exe' } });
  fireEvent.change(screen.getByLabelText('Arguments (JSON array)'), { target: { value: '["report.py", "--label", "a b"]' } });
  fireEvent.change(screen.getByLabelText('Working directory'), { target: { value: 'C:\\project' } });
  fireEvent.change(screen.getByLabelText('Schedule'), { target: { value: 'event' } });
  fireEvent.change(screen.getByLabelText('Event name'), { target: { value: 'training.checkpoint' } });
  fireEvent.change(screen.getByLabelText('Every N steps'), { target: { value: '500' } });
  fireEvent.change(screen.getByLabelText('Event filters (JSON object)'), { target: { value: '{"runId":"train-1"}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith(expect.objectContaining({ action: 'create', task: expect.objectContaining({ schedule: { kind: 'event', name: 'training.checkpoint', filters: { runId: 'train-1' }, stepModulo: 500, stepField: 'step' }, taskAction: { kind: 'worker', worker: { command: 'python.exe', args: ['report.py', '--label', 'a b'], cwd: 'C:\\project', usesGpu: false, longRunning: false, waitPolicy: 'when-idle' } } }) })));
});

it('shows real failure evidence and allows cancelling the identified run', async () => {
  const run = { id: 'run-1', taskId: 'task-1', taskName: 'Training', occurrence: 'once', status: 'failed', queuedAt: '2026-10-06T00:00:00Z', startedAt: '2026-10-06T00:00:01Z', finishedAt: '2026-10-06T00:00:02Z', scheduledAt: null, exitCode: 7, pid: 123, error: 'Worker exited with code 7', evidence: {} };
  const running = { ...run, id: 'run-2', status: 'running', finishedAt: null, exitCode: null, error: null };
  const command = vi.spyOn(jobs, 'backgroundCommand').mockImplementation(async args => args.action === 'logs' ? { stdout: 'epoch 5', stderr: 'out of memory', stdoutTruncated: false, stderrTruncated: false } : { ...empty, runs: [run, running] });
  render(<BackgroundJobs onNotice={vi.fn()} />);
  expect(await screen.findByText('Worker exited with code 7')).toBeVisible();
  expect(screen.getByText('Exit code 7')).toBeVisible();
  fireEvent.click(screen.getAllByRole('button', { name: 'Logs' })[0]);
  expect(await screen.findByText('out of memory')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith({ action: 'cancel', runId: 'run-2' }));
});

it('retains the saved policy when editing and sends explicit pause, resume and run actions', async () => {
  const task = { id: 'task-1', name: 'Saved job', conversationId: 'chat-1', schedule: { kind: 'interval', everySeconds: 600 }, taskAction: { kind: 'prompt', prompt: 'Review logs' }, context: { request: { approvalMode: 'ask-every-time' }, modelProfile: 'echo-3t', workspace: 'C:\\project' }, paused: false, nextDue: null };
  const command = vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue({ ...empty, tasks: [task] });
  render(<BackgroundJobs conversationId="chat-1" onNotice={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith({ action: 'pause', taskId: 'task-1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith({ action: 'run_now', taskId: 'task-1' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  expect(screen.getByText(/Saved approval: ask-every-time/)).toBeVisible();
  expect(screen.getByLabelText('Originating chat')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Agent prompt'), { target: { value: 'Review updated logs' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith(expect.objectContaining({ action: 'update', taskId: 'task-1', conversationId: 'chat-1' })));
  expect(JSON.stringify(command.mock.calls.find(([args]) => args.action === 'update'))).not.toMatch(/approvalMode|allow-all/);
});

it('explains that paused event jobs only run through Run now', async () => {
  const task = { id: 'task-1', name: 'Release check', conversationId: null,
    schedule: { kind: 'event', name: 'platform.release.verification' }, paused: true, nextDue: null,
    taskAction: { kind: 'worker', worker: { command: 'python.exe', args: ['verify.py'], cwd: 'C:\\project' } }, context: null };
  vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue({ ...empty, tasks: [task] });
  render(<BackgroundJobs onNotice={vi.fn()} />);
  expect(await screen.findByText('When event “platform.release.verification” arrives')).toBeVisible();
  expect(screen.getByText('Automatic triggers paused · Run now starts one manual run')).toBeVisible();
  expect(screen.queryByText('Waiting for the named event')).not.toBeInTheDocument();
  expect(screen.getByText('Runs the program below on this computer. Its exit code and output determine success.')).toBeVisible();
});

it('opens the exact originating chat from a job and from an older run', async () => {
  const task = { id: 'task-1', name: 'Review', conversationId: 'chat-task', schedule: { kind: 'interval', everySeconds: 600 }, taskAction: { kind: 'prompt', prompt: 'Review logs' }, context: null, paused: false, nextDue: null };
  const run = { id: 'run-1', taskId: 'deleted-task', taskName: 'Older review', conversationId: 'chat-run', occurrence: 'once', status: 'completed', queuedAt: '2026-10-08T00:00:00Z', startedAt: '2026-10-08T00:00:01Z', finishedAt: '2026-10-08T00:00:03Z', scheduledAt: null, exitCode: null, pid: null, error: null, evidence: {} };
  vi.mocked(jobs.backgroundCommand).mockResolvedValue({ ...empty, tasks: [task], runs: [run] });
  const opened: string[] = [];
  render(<BackgroundJobs onNotice={vi.fn()} onOpenConversation={id => opened.push(id)} />);
  const links = await screen.findAllByRole('button', { name: 'Open chat' });
  fireEvent.click(links[0]);
  fireEvent.click(links[1]);
  expect(opened).toEqual(['chat-task', 'chat-run']);
});

it('searches chats by title and saves the selected chat rather than the search text', async () => {
  const command = vi.mocked(jobs.backgroundCommand);
  render(<BackgroundJobs onNotice={vi.fn()} conversations={[{ id: 'chat-a', title: 'Alpha project' }, { id: 'chat-b', title: 'Checkpoint review' }]} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New job' }));
  fireEvent.change(screen.getByLabelText('Chat destination'), { target: { value: 'existing' } });
  fireEvent.change(screen.getByLabelText('Search chats'), { target: { value: 'checkpoint' } });
  expect(screen.queryByRole('option', { name: 'Alpha project' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Originating chat'), { target: { value: 'chat-b' } });
  fireEvent.change(screen.getByLabelText('Job name'), { target: { value: 'Review' } });
  fireEvent.change(screen.getByLabelText('Agent prompt'), { target: { value: 'Review the checkpoint' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await waitFor(() => expect(command).toHaveBeenCalledWith(expect.objectContaining({ action: 'create', conversationId: 'chat-b' })));
});

it('retains one new-chat identity if saving a recurring job needs a retry', async () => {
  const requests: jobs.BackgroundCommandArgs[] = [];
  vi.mocked(jobs.backgroundCommand).mockImplementation(async args => {
    if (args.action === 'create') { requests.push(args); throw new Error('Disk is busy'); }
    return empty;
  });
  render(<BackgroundJobs onNotice={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New job' }));
  expect((screen.getByLabelText('Chat destination') as HTMLSelectElement).value).toBe('new');
  fireEvent.change(screen.getByLabelText('Job name'), { target: { value: 'Daily review' } });
  fireEvent.change(screen.getByLabelText('Agent prompt'), { target: { value: 'Review the latest logs' } });
  fireEvent.change(screen.getByLabelText('Schedule'), { target: { value: 'interval' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await screen.findByText('Disk is busy');
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].newChat).toMatchObject({ id: expect.any(String) });
  expect(requests[1].newChat).toEqual(requests[0].newChat);
});

it('explains a short standalone worker and shows its actual output', async () => {
  const run = { id: 'run-1', taskId: 'task-1', taskName: 'Release check', conversationId: null, occurrence: 'manual:1', status: 'completed', queuedAt: '2026-10-08T00:00:00Z', startedAt: '2026-10-08T00:00:01Z', finishedAt: '2026-10-08T00:00:03Z', scheduledAt: null, exitCode: 0, pid: 123, error: null, evidence: { result: { outputSummary: 'stdout: Verification passed' } } };
  vi.mocked(jobs.backgroundCommand).mockResolvedValue({ ...empty, runs: [run] });
  render(<BackgroundJobs onNotice={vi.fn()} onOpenConversation={vi.fn()} />);
  expect(await screen.findByText('Program finished successfully · 2s')).toBeVisible();
  expect(screen.getByText('stdout: Verification passed')).toBeVisible();
  expect(screen.getByText(/Standalone program/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Open chat' })).toBeNull();
});

it('rejects a malformed argument array before submitting a worker', async () => {
  const command = vi.spyOn(jobs, 'backgroundCommand').mockResolvedValue(empty);
  render(<BackgroundJobs onNotice={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'New job' }));
  fireEvent.change(screen.getByLabelText('Action'), { target: { value: 'worker' } });
  fireEvent.change(screen.getByLabelText('Arguments (JSON array)'), { target: { value: '{"command":"bad"}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save job' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Arguments must be a JSON array of strings.');
  expect(command.mock.calls.some(([args]) => args.action === 'create')).toBe(false);
});
