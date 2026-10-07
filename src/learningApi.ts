import { invoke } from '@tauri-apps/api/core';
import type { LearningConfig } from './learning-config';
export interface LearningRecord {
  id: string; sourceKind: string; sourceId: string; conversationId?: string;
  revision: number; previousRecordId?: string; timestamp: string; sourceTimestamp?: string;
  ingestedAt: string; kind: string; role: string; source: string; title: string;
  content: string; contentBytes: number; contentSha256: string; sourceSha256: string; rawSha256?: string;
  evidenceLabel: string; status: string; metadata: Record<string, unknown>; raw: unknown;
  annotation?: unknown; training?: unknown;
}
export interface LearningRun {
  id: string; name: string; status: string; mode: string; conversationId?: string;
  createdAt: string; updatedAt: string; modelPath: string; config: LearningConfig;
  dataset: Record<string, unknown>; receipt?: Record<string, unknown> | null;
  error?: string | null; reviewError?: string | null; pid?: number | null;
  kind?: string; stage?: string; logNames?: string[]; environmentReceipt?: Record<string, unknown>;
}
export interface LearningSnapshot {
  runs: LearningRun[]; active: boolean; root: string; rawRecords: number;
  assistantConversationId?: string; draft?: {modelPath?: string; config?: Partial<LearningConfig>; verifiedOnly?: boolean} | null;
}
export interface RecordPage {records: LearningRecord[]; total: number; nextCursor: unknown | null; record?: LearningRecord}
export interface RawLogPage {path: string; content: string; nextOffset: number; totalBytes: number; complete: boolean; rawFilePreserved: boolean; available?: boolean}
export const learningDesktopAvailable = () => '__TAURI_INTERNALS__' in window;
export async function learningCommand<T = Record<string, unknown>>(args: Record<string, unknown>): Promise<T> {
  if (learningDesktopAvailable()) return invoke<T>('learning_command', {args});
  if (args.action === 'status') return {runs: [], active: false, root: '', rawRecords: 0} as T;
  if (['query','read','search'].includes(String(args.action))) return {records: [], total: 0, nextCursor: null} as T;
  throw new Error('Learning Studio needs the OpenCore desktop app for local records and training.');
}
