import { describe, expect, it } from "vitest";
import { buildResponseSegments } from "./response-segments";
import type { TimelineEntry } from "./types";

const row = (id: number, kind: string, title = kind, content = ""): TimelineEntry => ({
  id, conversationId: "c", timestamp: "2026-09-24T00:00:00Z", kind, role: kind === "tool_result" ? "tool" : "assistant",
  source: "OpenCore", title, content, metadata: {},
});
const shape = (events: TimelineEntry[]) => buildResponseSegments(events).map((segment) =>
  segment.type === "reasoning" ? `reasoning×${segment.entries.length}`
    : segment.type === "tools" ? `tools×${segment.steps.length}` : segment.type);

describe("buildResponseSegments", () => {
  it("merges back-to-back reasoning into one block", () => {
    expect(shape([row(1, "thinking"), row(2, "thinking"), row(3, "thinking")])).toEqual(["reasoning×3"]);
  });

  it("groups a tool loop without narration into one reasoning block and one tool group", () => {
    expect(shape([
      row(1, "thinking"), row(2, "tool_call", "browser_use"), row(3, "tool_result"),
      row(4, "thinking"), row(5, "tool_call", "browser_use"), row(6, "tool_result"),
    ])).toEqual(["reasoning×2", "inferred", "tools×2"]);
  });

  it("starts a new step at each narration, as the model describes its next action", () => {
    expect(shape([
      row(1, "thinking"), row(2, "progress", "Next action", "I can do that."),
      row(3, "thinking"), row(4, "progress", "Next action", "I'm going to look online."),
      row(5, "thinking"), row(6, "progress", "Next action", "I'm opening the web."),
      row(7, "tool_call", "browser_use"), row(8, "tool_result"), row(9, "tool_call", "browser_use"), row(10, "tool_result"),
    ])).toEqual(["reasoning×1", "narration", "reasoning×1", "narration", "reasoning×1", "narration", "tools×2"]);
  });

  it("pairs each call with its result and keeps an orphan result visible", () => {
    const segments = buildResponseSegments([row(1, "tool_call", "system_use"), row(2, "tool_result"), row(3, "tool_result")]);
    const tools = segments.find((segment) => segment.type === "tools");
    expect(tools?.type === "tools" && tools.steps.map((step) => [step.call.id, step.result?.id])).toEqual([[1, 2], [3, undefined]]);
  });

  it("ignores ECHO receipts, which render after the answer", () => {
    expect(shape([row(1, "thinking"), row(2, "echo"), row(3, "thinking")])).toEqual(["reasoning×2"]);
  });
});
