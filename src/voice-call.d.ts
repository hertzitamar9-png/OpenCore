import type { ReactNode } from "react";
import type { TimelineEntry, ChatQueueItem } from "./types";
export function voiceReply(entries: TimelineEntry[], submissionId: string): string;
export function runVoiceChat(text: string, signal: AbortSignal, options: {
  blocked?: string;
  currentId: () => string | undefined;
  settings: Omit<ChatQueueItem, "id" | "text" | "files" | "voice">;
  run: (item: ChatQueueItem) => Promise<boolean | undefined>;
  cancel: (id: string) => Promise<unknown>;
  computerUse?: boolean;
}): Promise<string>;
export function VoiceCall(props: {
  onClose: () => void;
  brainLabel: string;
  brainPicker: ReactNode;
  disabled?: string;
  sendTurn: (text: string, signal: AbortSignal, computerUse: boolean) => Promise<string>;
}): ReactNode;
