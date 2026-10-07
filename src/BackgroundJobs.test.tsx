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
