import type { TimelineEntry } from "./types";

export type ConversationTurn = {
  id: number;
  role: "user" | "assistant";
  entries: TimelineEntry[];
};

const RESPONSE_KINDS = new Set(["thinking", "progress", "tool_call", "tool_result", "message", "echo", "file", "error"]);

/** Group the stored activity of one response without changing the exact timeline. */
export function groupConversationTurns(entries: TimelineEntry[]): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (const entry of entries) {
    const isUserMessage = entry.kind === "message" && entry.role === "user";
    // Harness lifecycle records describe the SDK session, not a reply. Keep
    // them in the exact stored timeline, but never turn internal system events
    // into user-visible assistant messages.
    const isEchoImportProgress = entry.kind === "echo_import";
    if (!isUserMessage && !isEchoImportProgress && !(
      RESPONSE_KINDS.has(entry.kind) &&
      (entry.role === "assistant" || entry.role === "tool" || entry.kind === "echo" || entry.kind === "error")
    )) continue;
    const isResponseActivity = RESPONSE_KINDS.has(entry.kind) &&
      (entry.role === "assistant" || entry.role === "tool" || entry.kind === "echo" || entry.kind === "error");
    const previous = turns[turns.length - 1];
    const previousAssistantMessage = previous?.entries.filter((item) => item.kind === "message" && item.role === "assistant").at(-1);
    const startsNewSourceReply = entry.kind === "message" && entry.role === "assistant" &&
      previousAssistantMessage && previousAssistantMessage.source !== entry.source;
    if (isResponseActivity && !startsNewSourceReply && previous?.role === "assistant" &&
        previous.entries.every((item) => RESPONSE_KINDS.has(item.kind))) {
      previous.entries.push(entry);
    } else {
      turns.push({ id: entry.id, role: isUserMessage ? "user" : "assistant", entries: [entry] });
    }
  }
  return turns;
}
