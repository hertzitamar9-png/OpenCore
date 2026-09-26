import { describe, expect, it } from "vitest";
import { groupConversationTurns } from "./conversation-turns";
import { visibleEchoReceiptGroups } from "./response-segments";
import type { TimelineEntry } from "./types";

const event = (id: number, kind: string, role: string, content: string, source = "OpenCore"): TimelineEntry => ({
  id, conversationId: "chat", timestamp: "2026-09-23T00:00:00Z", kind, role, content,
  source, title: kind, metadata: {},
});

describe("conversation response grouping", () => {
  it("keeps reasoning, tools, answer, and ECHO in one assistant response", () => {
    const turns = groupConversationTurns([
      event(1, "message", "user", "Hi"),
      event(2, "thinking", "assistant", "Plan"),
      event(9, "progress", "assistant", "I'll check the open windows."),
      event(3, "tool_call", "assistant", "{}"),
      event(4, "tool_result", "tool", "{}", "LM Studio"),
      event(5, "message", "assistant", "Hello"),
      event(6, "echo", "system", "Saved", "ECHO"),
      event(7, "message", "user", "Next"),
      event(8, "message", "assistant", "Done"),
    ]);
    expect(turns.map((turn) => [turn.role, turn.entries.map((entry) => entry.id)])).toEqual([
      ["user", [1]], ["assistant", [2, 9, 3, 4, 5, 6]], ["user", [7]], ["assistant", [8]],
    ]);
  });

  it("shows three compaction receipts together after their response completes", () => {
    const receipts = [
      event(5, "echo", "system", "checkpoint one"),
      event(8, "echo", "system", "checkpoint two"),
      event(12, "echo", "system", "checkpoint three"),
    ];
    const turns = groupConversationTurns([
      event(1, "message", "user", "Fix the project"),
      event(2, "thinking", "assistant", "Plan"),
      event(3, "tool_call", "assistant", "{}"),
      event(4, "tool_result", "tool", "{}"),
      receipts[0],
      event(6, "thinking", "assistant", "Next step"),
      event(7, "tool_call", "assistant", "{}"),
      receipts[1],
      event(9, "message", "assistant", "I hit the compaction failure."),
      receipts[2],
      event(13, "error", "system", "Autocompact is thrashing"),
    ]);
    const response = turns[1];
    expect(response.role).toBe("assistant");
    expect(visibleEchoReceiptGroups(response.entries, true)).toEqual([]);
    expect(visibleEchoReceiptGroups(response.entries, false)).toEqual([receipts]);
  });

  it("keeps ECHO import progress visible and client replies in separate turns", () => {
    const turns = groupConversationTurns([
      event(1, "message", "assistant", "Codex answer", "Codex"),
      event(2, "echo_import", "system", "Indexing"),
      event(3, "message", "assistant", "OpenCore answer"),
    ]);
    expect(turns.map((turn) => turn.entries.map((entry) => entry.id))).toEqual([[1], [2], [3]]);
  });

  it("hides internal harness startup records so they cannot look like a reply", () => {
    const turns = groupConversationTurns([
      event(1, "message", "user", "What is this?"),
      event(2, "harness", "system", "Claude Agent SDK"),
      event(3, "message", "assistant", "It is the OpenCore logo."),
    ]);
    expect(turns.map((turn) => turn.entries.map((entry) => entry.content))).toEqual([
      ["What is this?"], ["It is the OpenCore logo."],
    ]);
  });
});
