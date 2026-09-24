export type RuntimeProfile = "stopped" | "echo" | "native1m" | "unsloth-echo";

export interface RuntimeSnapshot {
  profile: RuntimeProfile;
  status: string;
  startedAt?: string | null;
  gatewayPort: number;
  backendPort: number;
  echoPort: number;
  modelPid?: number | null;
  echoPid?: number | null;
  modelPath: string;
  archivePath: string;
  contextSize: number;
  error?: string | null;
  loadingPhase?: string;
  loadingStep?: number;
  loadingSteps?: number;
  loadingElapsedMs?: number | null;
}

export interface TelemetrySnapshot {
  gpuName: string;
  vramUsedMib: number;
  vramTotalMib: number;
  gpuUtilization: number;
  powerWatts: number;
  systemMemoryUsedMib: number;
  systemMemoryTotalMib: number;
  diskFreeGib: number;
  tokensPerSecond: number;
  promptTokens: number;
  completionTokens: number;
  totalPromptTokens?: number;
  totalCompletionTokens?: number;
  responseCount?: number;
  activeExperts: string[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  client: string;
  createdAt: string;
  updatedAt: string;
  messageCount: number;
  profile: string;
  status: string;
  project: string;
  projectId?: string | null;
  pinned: boolean;
}

export interface ProjectSummary {
  id: string;
  name: string;
  folderPath: string | null;
  needsFolder: boolean;
  folderAvailable: boolean;
  createdAt: string;
  updatedAt: string;
  conversationCount: number;
}

export interface OperationRecord {
  id: string;
  kind: string;
  target: string;
  phase: string;
  status: "queued" | "running" | "completed" | "failed";
  current: number;
  total: number;
  imported: number;
  updated: number;
  skipped: number;
  summary: string;
  error?: string | null;
  startedAt: string;
  finishedAt?: string | null;
}

export interface TimelineEntry {
  id: number;
  conversationId: string;
  timestamp: string;
  kind: string;
  role: string;
  source: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  level: string;
  source: string;
  message: string;
}

export interface ConnectorStatus {
  id: string;
  name: string;
  kind: string;
  status: string;
  endpoint: string;
  observable: boolean;
  details: string;
  custom: boolean;
}

export interface ConnectorInput {
  id?: string | null;
  name: string;
  kind: string;
  endpoint: string;
  matchPattern: string;
}

export interface AppSnapshot {
  runtime: RuntimeSnapshot;
  telemetry: TelemetrySnapshot;
  conversations: ConversationSummary[];
  projects: ProjectSummary[];
  logs: LogEntry[];
  connectors: ConnectorStatus[];
  activeConversationIds?: string[];
}

export interface ArchiveSearchHit {
  archiveFile: string;
  pageId: string;
  conversationId: string;
  offsetStart: number;
  preview: string;
}

export interface ArchivePageRef {
  archiveFile: string;
  pageId: string;
  conversationId: string;
  offsetStart: number;
  offsetEnd: number;
  timestamp: number;
}

export interface ArchiveEvent {
  eventId: string;
  conversationId: string;
  timestamp: number;
  kind: string;
  role: string;
  source: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
  contentBytes: number;
  truncated: boolean;
}

export interface ArchiveOverview {
  archives: number;
  pages: number;
  sourceBytes: number;
  storedBytes: number;
  conversations: { conversationId: string; pages: number; sourceBytes: number; storedBytes: number; lastTimestamp: number }[];
  summaries: { conversationId: string; generatedAt: number; sourcePages: number; modelCalls: number; truncated: boolean; incomplete: boolean; content: string }[];
  toolCalls?: number;
  toolResults?: number;
  imageAssets?: number;
  fileEvents?: number;
}

export interface ChatSendResult {
  conversationId: string;
  title: string;
}

export interface ChatQueueItem {
  id: string;
  text: string;
  files: string[];
  reasoningEffort: ReasoningEffort;
  approvalMode: ApprovalMode;
  skills: ("computer-use" | "chrome-control")[];
}

export type ReasoningEffort = "off" | "low" | "medium" | "high" | "extra-high" | "max" | "opencore";
export type ApprovalMode = "ask-every-time" | "approve-for-me" | "allow-chat" | "allow-all";
