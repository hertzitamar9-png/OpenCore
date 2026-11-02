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

async function learningBridge() {
  const probe=await invoke<{hubBridge?:{available:boolean;url:string;secret:string;authHeader?:string}}>('runtime_setup_probe');
  const bridge=probe.hubBridge;
  if(!bridge?.available)throw new Error('OpenCore’s local model helper is not available.');
  const url=new URL(bridge.url);
  if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||!url.port||!bridge.secret)throw new Error('The local model helper has an invalid address.');
  return bridge;
}
export async function autoLoopRequest(path:string,body?:Record<string,unknown>):Promise<unknown> {
  const bridge=await learningBridge();
  const response=await fetch(bridge.url+path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json',[bridge.authHeader||'X-OpenCore-Hub-Secret']:bridge.secret},...(body?{body:JSON.stringify(body)}:{})});
  const value=await response.json();
  if(!response.ok)throw new Error(value.error||'The AutoLoop request failed.');
  return value;
}
export async function registerAutoLoop(entry:unknown):Promise<string> {
  const model=entry as {id:string;familyLabel:string;quantization:string;contextTokens:number;providerId?:string};
  const bridge=await learningBridge();
  let provider=await invoke<{id:string;models:{id:string;profileId:string}[]}>('api_provider_save',{input:{...(model.providerId?{id:model.providerId}:{}),name:model.familyLabel,baseUrl:bridge.url+'/v1',model:model.id,contextTokens:model.contextTokens,apiKey:bridge.secret}});
  provider=await invoke('api_provider_model',{id:provider.id,model:model.id,enabled:true,manual:true});
  const profile=provider.models.find(value=>value.id===model.id);
  if(!profile)throw new Error('The verified model could not be registered for chat.');
  await autoLoopRequest('/catalog/profile',{id:model.id,providerId:provider.id,profileId:profile.profileId});
  return profile.profileId;
}
