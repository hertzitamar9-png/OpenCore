import { invoke } from '@tauri-apps/api/core';

export type BackgroundSchedule =
  | { kind: 'once'; at: string }
  | { kind: 'interval'; everySeconds: number; startAt?: string | null }
  | { kind: 'cron'; expression: string; timezone: 'utc' | 'local' }
  | { kind: 'event'; name: string; filters?: Record<string, unknown>; stepModulo?: number | null; stepField?: string };
export interface BackgroundWorker {
  command: string; args: string[]; cwd: string; usesGpu: boolean; longRunning: boolean;
  waitPolicy: 'when-idle' | 'allow-during-chat';
}
export type BackgroundAction = { kind: 'prompt'; prompt: string } | { kind: 'worker'; worker: BackgroundWorker };
export interface BackgroundContext {
  request: {
    conversationId: string; text: string; files: string[]; approvalMode: string; reasoningEffort: string;
    skills: string[]; subagentsEnabled: boolean; maxSubagents: number; projectSkillsEnabled: boolean; compactAtTokens: number;
  };
  modelProfile: string; workspace: string;
}
export interface BackgroundTask {
  id: string; name: string; conversationId: string | null; schedule: BackgroundSchedule; taskAction: BackgroundAction;
  context: BackgroundContext | null; paused: boolean; nextDue: number | null; revision: number; createdAt: string; updatedAt: string;
}
export interface BackgroundRun {
  id: string; taskId: string; taskName: string; conversationId?: string | null; occurrence: string;
  status: string; scheduledAt: string | null; queuedAt: string; startedAt: string | null; finishedAt: string | null;
  error: string | null; exitCode: number | null; pid: number | null; evidence: Record<string, unknown>;
}
export interface BackgroundSnapshot {
  tasks: BackgroundTask[]; runs: BackgroundRun[]; webhook: { url: string; token: string | null };
  execution: { appMustBeOpen: boolean; agentMustBeRunning?: boolean; windowCloseRequiresBackgroundAgent?: boolean; noPermanentService?: boolean; gpuWorkersHoldReservationUntilExit?: boolean };
}
export interface BackgroundLogs { stdout: string; stderr: string; stdoutTruncated: boolean; stderrTruncated: boolean; limitBytesPerStream?: number }
export interface BackgroundCommandArgs { action: string; conversationId?: string; taskId?: string; runId?: string; task?: unknown; event?: unknown }

const emptySnapshot: BackgroundSnapshot = { tasks: [], runs: [], webhook: { url: '', token: null }, execution: { appMustBeOpen: true } };
export async function backgroundCommand(args: BackgroundCommandArgs): Promise<unknown> {
  if (!('__TAURI_INTERNALS__' in window)) {
    if (args.action === 'list' || args.action === 'status') return emptySnapshot;
    throw new Error('Background execution is available in the installed OpenCore app.');
  }
  return invoke('background_command', { args });
}
export function describeSchedule(schedule: BackgroundSchedule): string {
  if (schedule.kind === 'once') return `Once · ${new Date(schedule.at).toLocaleString()}`;
  if (schedule.kind === 'interval') return `Every ${schedule.everySeconds} seconds`;
  if (schedule.kind === 'cron') return `${schedule.expression} · ${schedule.timezone === 'utc' ? 'UTC' : 'Local time'}`;
  return `When event “${schedule.name}” arrives${schedule.stepModulo ? ` · every ${schedule.stepModulo} steps` : ''}`;
}
export const backgroundError = (error: unknown): string => error instanceof Error ? error.message : String(error);
