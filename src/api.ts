import { invoke } from "@tauri-apps/api/core";
export const speechStart = (sessionId?: string) => invoke<string>("speech_start", { sessionId });
export interface ClaudeBridgeStatus { installed: boolean; connected: boolean; active: boolean; pluginPath: string; launchCommand: string; lastSeen: string | null; minimumVersion: string; enabled?: boolean; setupError?: string | null }
const emptyClaudeBridge: ClaudeBridgeStatus = { installed: false, connected: false, active: false, pluginPath: '', launchCommand: '', lastSeen: null, minimumVersion: '2.1.287' };
export const claudeBridgeStatus = (): Promise<ClaudeBridgeStatus> => desktop() ? invoke('claude_bridge_status') : Promise.resolve(emptyClaudeBridge);
export const installClaudeBridge = () => invoke<ClaudeBridgeStatus>('install_claude_bridge');
export interface MusicStudioStatus { installed: boolean; running: boolean; owned: boolean; url: string | null; folder: string; modelLoaded: boolean; integrationCurrent?: boolean; error: string | null }
export const musicStudioStatus = (): Promise<MusicStudioStatus> => desktop() ? invoke<MusicStudioStatus>('music_studio_status') : Promise.resolve({ installed: false, running: false, owned: false, url: null, folder: '', modelLoaded: false, error: null });
export const startMusicStudio = () => invoke<MusicStudioStatus>('start_music_studio');
export interface AppUpdateCheck { currentVersion: string; available: boolean; version: string | null }
export const checkLatestAppVersion = (): Promise<AppUpdateCheck> => desktop()
  ? invoke<AppUpdateCheck>('check_latest_app_version')
  : Promise.resolve({ currentVersion: 'web', available: false, version: null });
export const installLatestAppUpdate = () => invoke<void>('install_latest_app_update');
export interface StudioRequest { modelId: string; prompt: string; settings: Record<string, unknown>; conversationId?: string | null }
export interface StudioJob { id: string; category: string; request: StudioRequest; status: string; stage: string; createdAt: string; updatedAt: string; backendRun: string | null; progress: Record<string, unknown>; outputs: string[]; error: string | null }
export interface StudioRuntime { modelId: string; python: string; sourceDir: string | null; runner: string | null }
export const listStudioJobs = (): Promise<StudioJob[]> => desktop() ? invoke('list_studio_jobs') : Promise.resolve([]);
export const submitStudioJob = (request: StudioRequest) => invoke<StudioJob>('submit_studio_job', { request });
export const cancelStudioJob = (id: string) => invoke<void>('cancel_studio_job', { id });
export const configureStudioRuntime = (runtime: StudioRuntime) => invoke<void>('configure_studio_runtime', { runtime });
export const studioRuntime = (id: string): Promise<StudioRuntime | null> => desktop() ? invoke('studio_runtime', { id }) : Promise.resolve(null);
export const openStudioOutput = (id: string, path: string) => invoke<void>('open_studio_output', { id, path });
export const studioOutputPreview = (id: string, path: string) => invoke<{dataUrl: string; mime: string}>('studio_output_preview', { id, path });
export async function pickStudioFile(kind: 'python' | 'worker' | 'input'): Promise<string | null> {
  if (!desktop()) throw new Error('File selection requires the desktop application.');
  const value = await open({ multiple: false, directory: false, filters: kind === 'python' ? [{name:'Python interpreter',extensions:['exe']}] : kind === 'worker' ? [{name:'Runtime worker',extensions:['py']}] : undefined });
  return typeof value === 'string' ? value : null;
}
export async function pickStudioSourceDirectory(): Promise<string | null> {
  const value=await open({multiple:false,directory:true});return typeof value==='string'?value:null;
}
export interface SpeechStatus { modelId: string; installed: boolean; enabled: boolean; idleMode: "cold" | "ram"; workerReady: boolean; coldStartMs: number | null; warmWakeMs: number | null; phase: string; runtimePrecision?: "bf16" | "fp32"; loadingElapsedMs?: number | null; }
const defaultSpeechStatus: SpeechStatus = { modelId: "whisper-large-v3-turbo", installed: false, enabled: false, idleMode: "cold", workerReady: false, coldStartMs: null, warmWakeMs: null, phase: "off", runtimePrecision: "bf16", loadingElapsedMs: null };
export const speechStatus = () => desktop() ? invoke<SpeechStatus>("speech_status") : Promise.resolve(defaultSpeechStatus);
export const setSpeechEnabled = (enabled: boolean) => invoke<SpeechStatus>("speech_set_enabled", { enabled });
export const setSpeechIdleMode = (mode: "cold" | "ram") => invoke<SpeechStatus>("speech_set_idle_mode", { mode });
export const setSpeechModel = (modelId: string) => invoke<SpeechStatus>("speech_set_model", { modelId });
export const setSpeechRuntimePrecision = (precision: "bf16" | "fp32") => invoke<SpeechStatus>("speech_set_runtime_precision", { precision });
export interface EchoMemoryConfiguration { memoryTokens: number; refreshTokens: number; warmCacheMib: number; activeWindowTokens: number }
export interface EchoVirtualMemory {
  recent_tokens: number; pinned_tokens: number; retrieved_tokens: number; reserve_tokens: number;
  refreshes: number; page_faults: number; last_refresh_reason: string;
  retrieval_ms: number; rematerialization_prepare_ms: number; refresh_ms: number; prefill_ms?: number;
  materialization_cache_hits: number; materialization_cache_misses: number; materialization_ram_bytes: number;
  candidates_considered: number; diagnostics: string[];
  adapter: { adapter: string; materialization_mode: string; supports_direct_kv_reuse: boolean };
  active_pages: { page_id: string; source_hash: string; timestamp: number; tier: string; score: number; signals: Record<string, number> }[];
  virtual_history_tokens?: number; virtual_history_bytes?: number;
  source_bytes_read?: number; source_read_ms?: number;
  materialization_disk_bytes?: number;
}
export async function getEchoMemoryConfiguration(): Promise<EchoMemoryConfiguration> {
  return desktop() ? invoke("get_echo_memory_configuration") : { memoryTokens: 4096, refreshTokens: 128, warmCacheMib: 128, activeWindowTokens: 32768 };
}
export async function saveEchoMemoryConfiguration(configuration: EchoMemoryConfiguration): Promise<{ configuration: EchoMemoryConfiguration; applied: boolean }> {
  return desktop() ? invoke("save_echo_memory_configuration", { configuration }) : { configuration, applied: false };
}
export interface EchoWorkingSet { echoVirtualMemory?: EchoVirtualMemory | null; sdkContextTokens?: number; available: boolean; liveTokens?: number; promptTokens?: number; windowTokens: number; modelSessionTokens?: number; modelActiveTokens?: number; modelContextTokens?: number; modelSessionActive?: boolean; contextMode?: string; autoCompactThreshold?: number | null; autoCompactEnabled?: boolean; compactions?: number; offloadedMessages?: number; echoRecalledTokens?: number; echoActivePages?: number; echoActiveSourceHashes?: string[]; echoLastRetrievalReason?: string; echoRetrievalLatencyMs?: number; active?: boolean; warmCache?: { budgetBytes: number; residentBytes: number; pages: number; hits: number; misses: number; evictions: number; oversized: number; hitRate: number }; harness?: { name: string; status: string; tasks: { id: number; desc: string; status: string }[]; reviews: number; toolCount: number; unverified: string[]; ledgerPath: string } }
export async function echoWorkingSet(conversationId: string): Promise<EchoWorkingSet> {
  if (!desktop()) return { available: false, windowTokens: 262144 };
  return invoke<EchoWorkingSet>("echo_working_set", { conversationId });
}
export const speechTranscribe = (sessionId: string, audio: string) => invoke<{ text: string; language: string }>("speech_transcribe", { sessionId, audio });
export const speechCancel = (sessionId: string) => invoke<void>("speech_cancel", { sessionId });
import { open } from "@tauri-apps/plugin-dialog";
import type { AppSnapshot, ArchiveEvent, ArchiveOverview, ArchivePageRef, ArchiveSearchHit, ApprovalMode, ChatSendResult, ConnectorInput, ConnectorStatus, OperationRecord, ProjectSummary, ReasoningEffort, RuntimeProfile, TimelineEntry } from "./types";
import { previewSnapshot, previewTimeline } from "./mock";
import type { ImportFormat, ImportPreview, ImportReport } from "./chat-import-types";
import modelCatalog from "../src-tauri/resources/model-catalog.json";

const desktop = () => "__TAURI_INTERNALS__" in window;

export interface InstalledModel {
  id: string; label: string; description: string; selectable: boolean;
  precision: string; contextTokens: number; license: string; experimental: boolean; note: string;
  installed: boolean; externalManaged: boolean; downloadBytes: number; totalBytes: number;
  speechLanguage?: string;
  category?: string; backend?: string; runtimeReady?: boolean; runtimeConnected?: boolean; installable?: boolean; sourceUrl?: string; setupUrl?: string;
  variantOf?: string; memoryMode?: "native" | "echo"; runtimeModelPath?: string; visionProjectorPath?: string;
  vramWeightMultiplier?: number; weightBytes?: number;
  artifactIdentity?: string | null;
  runtimePrecision?: { sourceFormat: string; runtimeDtype: string; estimatedRuntimeBytes: number; runtimeMemoryNote: string; runtimeComponent: string };
  sourceDownloaded?: boolean; preparedReady?: boolean;
  preparedRuntime?: { kind: string; path: string; sha256: string; bytes: number; sourceRepo: string; sourceRevision: string; sourceFilename: string; sourceSha256: string; sourceBytes: number; conversionManifestSha256: string };
}
export interface ModelLibrary {
  models: InstalledModel[]; diskFreeBytes: number; minimumFreeBytes: number;
  progress: { modelId: string; phase: string; downloadedBytes: number; totalBytes: number; currentFile: string; error: string | null } | null;
}
export async function modelLibrary(): Promise<ModelLibrary> {
  if (desktop()) return invoke<ModelLibrary>("list_model_library");
  return { models: modelCatalog.models.map((model) => {
    const files = modelCatalog.artifacts.filter((file) => model.artifacts.includes(file.id));
    const exactBytes = files.reduce((sum, file) => sum + file.bytes, 0);
    const artifactIdentity = files.length ? JSON.stringify(files.map(file =>
      [file.path, file.repo, file.revision, file.filename, file.sha256, file.bytes]).sort((a, b) =>
        JSON.stringify(a) < JSON.stringify(b) ? -1 : JSON.stringify(a) > JSON.stringify(b) ? 1 : 0)) : null;
    const pinnedWeights = "weightArtifacts" in model ? model.weightArtifacts : undefined;
    const weightFiles = pinnedWeights?.length ? files.filter(file => pinnedWeights.includes(file.id)) : model.runtimeModelPath
      ? files.filter(file => file.path === model.runtimeModelPath || file.path === model.visionProjectorPath)
      : files.filter(file => /\.(gguf|safetensors|bin|pt|pth|ckpt|onnx|model|tflite)$/i.test(file.filename));
    return { ...model, memoryMode: model.memoryMode === "echo" ? "echo" as const : "native" as const,
      installed: false, externalManaged: false, downloadBytes: exactBytes, totalBytes: exactBytes, artifactIdentity,
      weightBytes: weightFiles.length ? weightFiles.reduce((sum, file) => sum + file.bytes, 0) : exactBytes };
  }),
    diskFreeBytes: 240e9, minimumFreeBytes: 64 * 1024 * 1024, progress: null };
}
export const installedSkillModels = (): Promise<{id:string;category:string;installed:boolean;runtimeConnected?:boolean}[]> => desktop() ? invoke('installed_skill_models') : Promise.resolve([]);
export async function installModel(id: string): Promise<void> {
  if (!desktop()) throw new Error("Model installation requires the desktop application.");
  await invoke("install_model", { id });
}
export interface ModelRemovalFile { path: string; bytes: number; external: boolean; sharedWith: string[] }
export interface ModelRemovalPlan { modelId: string; label: string; files: ModelRemovalFile[]; retainedFiles: ModelRemovalFile[]; totalBytes: number; confirmationToken: string }
export async function modelRemovalPlan(id: string): Promise<ModelRemovalPlan> {
  if (!desktop()) throw new Error("Model deletion requires the desktop application.");
  return invoke<ModelRemovalPlan>("model_removal_plan", { id });
}
export async function uninstallModel(id: string, confirmationToken: string): Promise<void> {
  if (!desktop()) throw new Error("Model installation requires the desktop application.");
  await invoke("uninstall_model", { id, confirmationToken });
}
export const cancelModelInstall = () => invoke<void>("cancel_model_install");

export type ArtifactPreview = { id: string; name: string; mime: string; size: number; dataUrl: string; text: string | null };
export type ComposerAttachmentPreview = { name: string; mime: string; size: number; dataUrl: string; text: string | null };
export type BrowserStatus = { port: number; token: string; connected: boolean; enabled?: boolean; extensionPath?: string };
export type ComputerAppPermission = {path: string; name: string; access: 'allow' | 'deny'};
export type ComputerAccess = {enabled: boolean; apps: ComputerAppPermission[]; revision?: number};
export type ComputerAccessWindow = {windowId: number; title: string; path: string; name: string; pid?: number};
export const computerAccess = (): Promise<ComputerAccess> => desktop() ? invoke('computer_access') : Promise.resolve({enabled:false, apps:[], revision:0});
export const computerAccessWindows = (): Promise<ComputerAccessWindow[]> => desktop() ? invoke('computer_access_windows') : Promise.resolve([]);
export const setComputerAccess = (policy: ComputerAccess) => invoke<ComputerAccess>('set_computer_access', {policy});
export const allowComputerWindow = (windowId: number) => invoke<ComputerAccess>('allow_computer_window', {windowId});
export const setBrowserAccess = (enabled: boolean) => invoke<BrowserStatus>('set_browser_access', {enabled});
export type BrowserTab = { tabId: number; title: string; url: string; active: boolean };
export type BrowserShot = { tabId: number; dataUrl: string; viewport: { width: number; height: number } };
export type DesktopWindow = { windowId: number; title: string; permission?: 'allowed'|'ask'; application?: string; bounds: { left: number; top: number; width: number; height: number } };
export type DesktopShot = { windowId: number; bounds: DesktopWindow["bounds"]; origin?: { x: number; y: number }; dataUrl: string };

export async function desktopCommand<T>(action: string, args: Record<string, unknown> = {}): Promise<T> {
  if (!desktop()) throw new Error("Windows control requires the desktop application.");
  return invoke<T>("desktop_command", { action, args });
}

export async function setComputerFocusMode(keepUserWindowInFront: boolean): Promise<void> {
  if (desktop()) await invoke("set_computer_focus_mode", { keepUserWindowInFront });
}

export async function browserBridgeStatus(): Promise<BrowserStatus> {
  return desktop() ? invoke<BrowserStatus>("browser_bridge_status") : { port: 8814, token: "", connected: false };
}

export async function browserCommand<T>(action: string, args: Record<string, unknown> = {}): Promise<T> {
  if (!desktop()) throw new Error("Browser control requires the desktop application.");
  return invoke<T>("browser_command", { action, args });
}

export async function nativeBrowserCommand<T>(action: string, args: Record<string, unknown> = {}): Promise<T> {
  if (!desktop()) throw new Error("The in-app browser requires the desktop application.");
  return invoke<T>("native_browser_command", { action, args });
}

export async function previewArtifact(id: string): Promise<ArtifactPreview> {
  if (!desktop()) throw new Error("Artifact preview requires the desktop application.");
  return invoke<ArtifactPreview>("preview_artifact", { id });
}

export async function previewAttachmentImage(path: string): Promise<string> {
  if (!desktop()) throw new Error("Image preview requires the desktop application.");
  return invoke<string>("preview_attachment_image", { path });
}

export async function previewComposerAttachment(path: string): Promise<ComposerAttachmentPreview> {
  if (!desktop()) throw new Error("File preview requires the desktop application.");
  return invoke<ComposerAttachmentPreview>("preview_composer_attachment", { path });
}

export async function stageComposerAttachment(file: File): Promise<string> {
  if (!desktop()) throw new Error("Pasting file attachments requires the OpenCore desktop app.");
  const maximumBytes = 32 * 1024 * 1024;
  if (file.size > maximumBytes) throw new Error("Pasted and dropped files are limited to 32 MiB. Use Attach files to select larger files directly.");

  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  const extensions: Record<string, string> = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp", "application/pdf": "pdf",
  };
  const extension = extensions[file.type] || "";
  const name = file.name.trim()
    ? /\.[^./\\]+$/.test(file.name) || !extension ? file.name : `${file.name}.${extension}`
    : `pasted-${Date.now()}.${extension || "bin"}`;
  return invoke<string>("stage_composer_attachment", { name, dataBase64: window.btoa(binary) });
}

export async function downloadArtifact(id: string): Promise<string> {
  if (!desktop()) throw new Error("Artifact download requires the desktop application.");
  return invoke<string>("download_artifact", { id });
}

export async function snapshot(): Promise<AppSnapshot> {
  if (desktop()) return invoke<AppSnapshot>("get_snapshot");
  const preview = structuredClone(previewSnapshot);
  const params = new URLSearchParams(window.location.search);
  if (params.has("previewActive") || params.has("previewReasoning")) preview.activeConversationIds = ["preview"];
  return preview;
}

export async function conversation(id: string): Promise<TimelineEntry[]> {
  if (desktop()) return invoke<TimelineEntry[]>("get_conversation", { id });
  if (id !== "preview") return [];
  return new URLSearchParams(window.location.search).has("previewReasoning")
    ? previewTimeline.filter((entry) => entry.id <= 4.5) : previewTimeline;
}

export async function startProfile(profile: RuntimeProfile, attachUrl?: string): Promise<void> {
  if (!desktop()) throw new Error("Runtime controls require the desktop application.");
  await invoke("start_profile", { request: { profile, attachUrl: attachUrl || null } });
}

export async function selectProfile(profile: RuntimeProfile): Promise<void> {
  if (!desktop()) return;
  if (profile === "stopped") throw new Error("Choose a model profile before starting the runtime.");
  await invoke("select_profile", { profile });
}

export async function stopRuntime(): Promise<void> {
  if (!desktop()) throw new Error("Runtime controls require the desktop application.");
  await invoke("stop_runtime");
}

export async function restartRuntime(): Promise<void> {
  if (!desktop()) throw new Error("Runtime controls require the desktop application.");
  await invoke("restart_runtime");
}

export async function exportConversation(id: string, format: "json" | "markdown"): Promise<string> {
  if (!desktop()) throw new Error("Export requires the desktop application.");
  const result = await invoke<{ path: string }>("export_conversation", { id, format });
  return result.path;
}
export const registerPreparedModel = (id: string, path: string, manifestPath: string) => invoke<void>("register_prepared_model", { id, path, manifestPath });
export async function choosePreparedModelFiles(): Promise<{ path: string; manifestPath: string } | null> {
  if (!desktop()) throw new Error("Prepared model setup requires the desktop application.");
  const path = await open({ multiple: false, directory: false, title: "Choose the prepared Woof GGUF", filters: [{ name: "Prepared GGUF", extensions: ["gguf"] }] });
  if (typeof path !== 'string') return null;
  const manifestPath = await open({ multiple: false, directory: false, title: "Choose its adjacent conversion manifest", defaultPath: path.replace(/[\\/][^\\/]+$/, ''), filters: [{ name: "Conversion manifest", extensions: ["json"] }] });
  return typeof manifestPath === 'string' ? { path, manifestPath } : null;
}

export interface ImportedConversationPage {
  conversations: import("./types").ConversationSummary[];
  total: number;
  offset: number;
  limit: number;
}

export type ImportedConversationSource = "all" | "hermes" | "opencode" | "codex" | "claude" | "other";

export async function listImportedConversations(query = "", offset = 0, limit = 100, source: ImportedConversationSource = "all"): Promise<ImportedConversationPage> {
  if (!desktop()) return { conversations: [], total: 0, offset, limit };
  return invoke<ImportedConversationPage>("list_imported_conversations", { query, offset, limit, source });
}

export async function importedConversationSummary(id: string): Promise<import("./types").ConversationSummary | null> {
  if (!desktop()) return null;
  return invoke<import("./types").ConversationSummary | null>("get_imported_conversation_summary", { id });
}

export async function previewChatFile(path: string, format: ImportFormat): Promise<ImportPreview> {
  if (!desktop()) throw new Error("Chat imports require the OpenCore desktop application.");
  return invoke<ImportPreview>("preview_chat_file", {path, format});
}

export async function importChatFile(path: string, format: ImportFormat, requestId: string): Promise<ImportReport> {
  if (!desktop()) throw new Error("Chat imports require the OpenCore desktop application.");
  return invoke<ImportReport>("import_chat_file", {path, format, requestId});
}

export async function cancelChatFileImport(requestId: string): Promise<boolean> {
  if (!desktop()) return false;
  return invoke<boolean>("cancel_chat_file_import", {requestId});
}

export async function renameConversation(id: string, title: string): Promise<void> {
  if (!desktop()) return;
  await invoke("rename_conversation", { id, title });
}

export async function removeConversation(id: string): Promise<void> {
  if (!desktop()) return;
  await invoke("delete_conversation", { id });
}

export async function setConversationPinned(id: string, pinned: boolean): Promise<void> {
  if (!desktop()) return;
  await invoke("set_conversation_pinned", { id, pinned });
}

export async function moveConversationToProject(id: string, projectId: string | null): Promise<void> {
  if (!desktop()) return;
  await invoke("move_conversation_to_project", { id, projectId });
}

export async function chooseProjectFolder(): Promise<string | null> {
  if (!desktop()) throw new Error("Choose a folder in the Windows desktop app.");
  const selected = await open({ directory: true, multiple: false, title: "Choose an OpenCore project folder" });
  return typeof selected === "string" ? selected : null;
}

export async function createProject(name: string, folderPath: string): Promise<ProjectSummary> {
  if (!desktop()) throw new Error("Project creation requires the Windows desktop app.");
  return invoke<ProjectSummary>("create_project", { name, folderPath });
}

export async function changeProjectFolder(id: string, folderPath: string): Promise<ProjectSummary> {
  if (!desktop()) throw new Error("Project changes require the Windows desktop app.");
  return invoke<ProjectSummary>("change_project_folder", { id, folderPath });
}

export async function renameProject(id: string, name: string): Promise<ProjectSummary> {
  if (!desktop()) throw new Error("Project changes require the desktop application.");
  return invoke<ProjectSummary>("rename_project", { id, name });
}

export async function deleteProject(id: string): Promise<number> {
  if (!desktop()) throw new Error("Project changes require the desktop application.");
  return invoke<number>("delete_project", { id });
}

export async function saveConnector(input: ConnectorInput): Promise<ConnectorStatus> {
  if (!desktop()) return { id: input.id || "preview-custom", name: input.name, kind: input.kind, endpoint: input.endpoint, status: "configured", observable: false, details: "Preview connector", custom: true };
  return invoke<ConnectorStatus>("save_connector", { input });
}

export async function deleteConnector(id: string): Promise<void> {
  if (!desktop()) return;
  await invoke("delete_connector", { id });
}

export async function testConnector(id: string, endpoint: string): Promise<string> {
  if (!desktop()) return "Desktop backend required for a live connection test.";
  return invoke<string>("test_connector", { id, endpoint });
}

export async function configureUnsloth(): Promise<string> {
  if (!desktop()) return "Desktop backend required to configure Unsloth.";
  return invoke<string>("configure_unsloth");
}

export async function syncLocalHistory(id: string): Promise<string> {
  if (!desktop()) return "Desktop backend required.";
  return invoke<string>("sync_local_history", { id });
}

export async function listOperations(): Promise<OperationRecord[]> {
  return desktop() ? invoke<OperationRecord[]>("list_operations") : [];
}

export type AgentConnectorId = "claude-code" | "codex" | "opencode" | "hermes";
export async function startHistorySync(id: AgentConnectorId): Promise<OperationRecord> {
  if (!desktop()) throw new Error("History sync requires the desktop application.");
  return invoke<OperationRecord>("start_history_sync", { id });
}

export async function cancelHistorySync(id: string): Promise<void> {
  if (!desktop()) throw new Error("History import cancellation requires the desktop application.");
  await invoke("cancel_history_sync", { id });
}

export async function clearImportedHistory(id: AgentConnectorId): Promise<string> {
  if (!desktop()) throw new Error("Imported history cleanup requires the desktop application.");
  return invoke<string>("clear_imported_history", { id });
}

export async function searchArchive(query: string, limit = 50, conversationIds?: string[]): Promise<ArchiveSearchHit[]> {
  if (!desktop()) return [];
  return invoke<ArchiveSearchHit[]>("search_archive", { query, limit, conversationIds });
}

export async function archiveOverview(): Promise<ArchiveOverview> {
  if (!desktop()) return { archives: 0, pages: 0, sourceBytes: 0, storedBytes: 0, conversations: [], summaries: [] };
  return invoke<ArchiveOverview>("archive_overview");
}

export async function readArchivePage(archiveFile: string, pageId: string): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("read_archive_page", { archiveFile, pageId });
}

export async function listArchivePages(conversationId: string, offset = 0, limit = 40): Promise<ArchivePageRef[]> {
  if (!desktop()) return [];
  return invoke<ArchivePageRef[]>("list_archive_pages", { conversationId, offset, limit });
}

export async function listArchiveEvents(conversationId: string, offset = 0, limit = 100): Promise<ArchiveEvent[]> {
  if (!desktop()) return [];
  return invoke<ArchiveEvent[]>("list_archive_events", { conversationId, offset, limit });
}

export async function readArchiveEvent(eventId: string): Promise<ArchiveEvent> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<ArchiveEvent>("read_archive_event", { eventId });
}

export async function readArchiveAsset(assetId: string): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("read_archive_asset", { assetId });
}

export async function indexEchoHistory(): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("index_echo_history");
}

export async function echoMemoryAction(action: "trim" | "compact", conversationId: string): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("echo_memory_action", { action, conversationId });
}

export async function clearLogs(): Promise<void> {
  if (!desktop()) return;
  await invoke("clear_logs");
}

export async function verifyModel(): Promise<string> {
  if (!desktop()) return "Desktop backend required.";
  return invoke<string>("verify_model");
}

export async function openLocalPath(path: string): Promise<void> {
  if (!desktop()) throw new Error("Opening local folders requires the Windows application.");
  await invoke("open_local_path", { path });
}

export async function exportArchiveIndex(): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("export_archive_index");
}

export async function exportDiagnostics(): Promise<string> {
  if (!desktop()) throw new Error("Desktop backend required.");
  return invoke<string>("export_diagnostics");
}

export async function healthCheck(): Promise<string> {
  if (!desktop()) return "Desktop backend required.";
  return invoke<string>("health_check");
}

export async function configureAgentConnector(id: AgentConnectorId, profileFolder?: string): Promise<string> {
  if (!desktop()) return "Desktop backend required.";
  return invoke<string>("configure_agent_connector", { id, profileFolder });
}

export async function setAgentConnectorFolder(id: 'opencode' | 'hermes', folder: string): Promise<string> {
  if (!desktop()) throw new Error('Agent connectors require the desktop application.');
  return invoke<string>('set_agent_connector_folder', { id, folder });
}

export async function sendChatMessage(conversationId: string, text: string, files: string[], reasoningEffort: ReasoningEffort, approvalMode: ApprovalMode, skills: string[] = [], subagentsEnabled = false, maxSubagents = 3, projectSkillsEnabled = true, compactAtTokens = 200000, submissionId = crypto.randomUUID()): Promise<ChatSendResult> {
  if (!desktop()) throw new Error("Interactive chat requires the desktop application.");
  return invoke<ChatSendResult>("send_chat_message", {
    request: { conversationId, text, files, reasoningEffort, approvalMode, skills, subagentsEnabled, maxSubagents, projectSkillsEnabled, compactAtTokens, submissionId },
  });
}

export async function resolveToolApproval(requestId: string, approved: boolean): Promise<void> {
  if (!desktop()) return;
  await invoke("resolve_tool_approval", { requestId, approved });
}

export async function cancelChatMessage(conversationId: string): Promise<boolean> {
  if (!desktop()) return false;
  return invoke<boolean>("cancel_chat_message", { conversationId });
}
