export type ImportFormat = "auto" | "opencore" | "hermes" | "codex" | "claude" | "generic";

export type ImportConversationResult = {
  conversationId: string;
  sourceConversationId: string;
  title: string;
  status: "imported" | "updated" | "skipped" | "failed";
  entries: number;
  warnings: string[];
  error?: string | null;
};

export type ImportReport = {
  sourceFormat: string;
  sourcePath: string;
  imported: number;
  updated: number;
  skipped: number;
  failed: number;
  current: number;
  total: number;
  cancelled: boolean;
  warnings: string[];
  conversations: ImportConversationResult[];
};

export type ImportPreview = {
  sourceFormat: string;
  sourcePath: string;
  conversations: number;
  entries: number;
  warnings: string[];
  samples: Array<{ sourceConversationId: string; title: string; entries: number; warnings: string[]; error?: string | null }>;
};
