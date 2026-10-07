import { invoke } from '@tauri-apps/api/core';
import recipes from '../src-tauri/resources/runtime-setup/recipes.json';

export interface SetupRecipe {
  id: string; label: string; kind: string; modelIds: string[]; packages: Record<string, string>;
  minimumDiskBytes: number; minimumRamBytes: number; requiresCuda: boolean;
  requiresLicenseAcceptance?: boolean; requiresAdministrator?: boolean;
  sourceUrls: string[]; limitations: string;
}
export interface SetupReceipt {
  schema: number; targetId: string; recipeId: string; recipeFingerprint: string;
  verifiedAt: string; dependenciesVerified: boolean; inferenceVerified: boolean;
  python?: string; executable?: string; runner?: string; environmentVerified?: boolean;
  bootVerified?: boolean; guestVerified?: boolean; sdkRoot?: string; avdHome?: string;
  avdName?: string; emulatorExecutable?: string; vmName?: string; guestUser?: string; passwordEnv?: string;
  inferenceProof?: {jobId: string; checkedAt: string}; sourceUrls: string[];
}
export interface SetupJob {
  id: string; targetId: string; recipeId: string; status: string; stage: string; detail: string;
  createdAt: string; updatedAt: string; downloadedBytes: number; totalBytes: number;
  diagnostics: string[]; error: string | null; receipt: SetupReceipt | null;
}
export interface SetupSnapshot {
  recipes: SetupRecipe[]; jobs: SetupJob[]; receipts: SetupReceipt[];
  activeJobId: string | null; managedRoot: string;
}
export interface SetupOptions {
  installWeights: boolean; acceptLicenses?: boolean; allowAdministrator?: boolean;
  isoPath?: string; guestUser?: string; passwordEnv?: string;
}
export interface SetupInventory {
  python: {path: string; version: number[]; compatible: boolean; managed: boolean}[];
  pythonError?: string; ramBytes?: number; diskFreeBytes?: number;
  gpus: {name: string; totalBytes: number; freeBytes: number; driver: string}[];
  tools: {android?: {sdkRoot: string; adb?: string; emulator?: string; avds?: string[]; accelerationUsable?: boolean}[];
    java?: {path: string; major: number; version: string}[];
    virtualbox?: {executable: string; version?: string; vms?: string; error?: string} | null};
}
export const setupDesktopAvailable = () => '__TAURI_INTERNALS__' in window;
export const runtimeSetupStatus = (): Promise<SetupSnapshot> => setupDesktopAvailable()
  ? invoke('runtime_setup_status') : Promise.resolve({recipes: recipes.recipes as SetupRecipe[], jobs: [], receipts: [], activeJobId: null, managedRoot: ''});
export const runtimeSetupProbe = (): Promise<SetupInventory> => setupDesktopAvailable()
  ? invoke('runtime_setup_probe') : Promise.resolve({python: [], gpus: [], tools: {}});
export const runtimeSetupStart = (targetId: string, options: SetupOptions): Promise<SetupJob> => invoke('runtime_setup_start', {targetId, options});
export const runtimeSetupCancel = (jobId: string): Promise<void> => invoke('runtime_setup_cancel', {jobId});
export const runtimeSetupRecordInference = (jobId: string): Promise<SetupReceipt> => invoke('runtime_setup_record_inference', {jobId});
export const isSetupActive = (job: SetupJob | undefined) => !!job && ['queued', 'running', 'cancelling'].includes(job.status);
export const hasManagedRuntime = (targetId: string) => recipes.recipes.some(recipe => recipe.modelIds.includes(targetId));
