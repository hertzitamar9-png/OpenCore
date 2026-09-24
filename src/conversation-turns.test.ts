import { describe, expect, it } from "vitest";
import { groupConversationTurns } from "./conversation-turns";
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

  it("leaves imported client messages and ECHO import progress separate", () => {
    const turns = groupConversationTurns([
      event(1, "message", "assistant", "Codex answer", "Codex"),
      event(2, "echo_import", "system", "Indexing"),
      event(3, "message", "assistant", "OpenCore answer"),
    ]);
    expect(turns.map((turn) => turn.entries.map((entry) => entry.id))).toEqual([[1], [2], [3]]);
  });
});
