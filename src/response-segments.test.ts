import { describe, expect, it } from "vitest";
import { buildResponseSegments, visibleEchoReceiptGroups } from "./response-segments";
import type { TimelineEntry } from "./types";

const row = (id: number, kind: string, title = kind, content = ""): TimelineEntry => ({
  id, conversationId: "c", timestamp: "2026-09-24T00:00:00Z", kind, role: kind === "tool_result" ? "tool" : "assistant",
  source: "OpenCore", title, content, metadata: {},
});
const shape = (events: TimelineEntry[]) => buildResponseSegments(events).map((segment) =>
  segment.type === "reasoning" ? `reasoning×${segment.entries.length}`
    : segment.type === "tools" ? `tools×${segment.steps.length}` : segment.type);

describe("buildResponseSegments", () => {
  it("pairs concurrent SDK calls with their own results even when completion order differs", () => {
    const segments = buildResponseSegments([
      row(1,"tool_call","Read",'{"id":"a"}'), row(2,"tool_call","Read",'{"id":"b"}'),
      row(3,"tool_result","Read",'{"toolCallId":"b","content":"second"}'),
      row(4,"tool_result","Read",'{"toolCallId":"a","error":"missing file"}'),
    ]);
    const tools = segments.find(segment => segment.type === "tools");
    expect(tools?.type === "tools" && tools.steps.map(step => [step.call.id,step.result?.id])).toEqual([[1,4],[2,3]]);
  });
  it("attaches the real failed result even when a failure explanation intervenes", () => {
    const segments = buildResponseSegments([row(1,"tool_call","dev"),row(2,"progress","Check failed","File exists"),row(3,"tool_result","dev",'{"error":"File exists"}')]);
    const tools = segments.find(segment => segment.type === "tools");
    expect(tools?.type === "tools" && tools.steps[0].result?.content).toBe('{"error":"File exists"}');
    expect(segments.filter(segment => segment.type === "tools")).toHaveLength(1);
  });
  it("merges back-to-back reasoning into one block", () => {
    expect(shape([row(1, "thinking"), row(2, "thinking"), row(3, "thinking")])).toEqual(["reasoning×3"]);
  });

  it("keeps streamed answer text between reasoning blocks in event order", () => {
    const segments = buildResponseSegments([
      row(1, "thinking", "Thinking", "first thought"),
      row(2, "message", "Live answer", "text in the middle"),
      row(3, "thinking", "Thinking", "latest thought"),
    ]);
    expect(segments.map((segment) => segment.type === "reasoning"
      ? segment.entries[0].content
      : segment.type === "entry" ? segment.entry.content : segment.type)).toEqual([
      "first thought", "text in the middle", "latest thought",
    ]);
  });

  it("keeps later reasoning below the preceding tool result even without narration", () => {
    expect(shape([
      row(1, "thinking"), row(2, "tool_call", "browser_use"), row(3, "tool_result"),
      row(4, "thinking"), row(5, "tool_call", "browser_use"), row(6, "tool_result"),
    ])).toEqual(["reasoning×1", "inferred", "tools×1", "reasoning×1", "inferred", "tools×1"]);
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

  it("keeps ECHO receipts out of activity and groups all receipt records into one post-response card", () => {
    const receipts = [row(2, "echo", "First compact", "first receipt"), row(3, "echo", "Second compact", "second receipt"), row(4, "echo", "Third compact", "third receipt")];
    expect(shape([row(1, "thinking"), ...receipts, row(5, "thinking")])).toEqual(["reasoning×2"]);
    expect(visibleEchoReceiptGroups(receipts, true)).toEqual([]);
    expect(visibleEchoReceiptGroups(receipts, false)).toEqual([receipts]);
  });
});
