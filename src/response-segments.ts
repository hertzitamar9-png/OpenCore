import type { TimelineEntry } from "./types";

export type ToolStep = { call: TimelineEntry; result?: TimelineEntry };

export type ResponseSegment =
  | { type: "narration"; key: string; entry: TimelineEntry }
  | { type: "inferred"; key: string; call: TimelineEntry }
  | { type: "reasoning"; key: string; entries: TimelineEntry[] }
  | { type: "tools"; key: string; steps: ToolStep[] }
  | { type: "entry"; key: string; entry: TimelineEntry };

/**
 * Fold one response's activity into readable steps.
 *
 * Narration (the model's short "I'm opening the browser" sentences) separates
 * steps. Between two narrations, all reasoning reads as one "Reasoned" block and
 * all tool calls as one "Used N tools" group, so a long tool loop no longer shows
 * as a stack of identical rows. A run with tools but no narration gets one
 * sentence inferred from its first recorded call.
 */
export function buildResponseSegments(events: TimelineEntry[]): ResponseSegment[] {
  const segments: ResponseSegment[] = [];
  let reasoning: Extract<ResponseSegment, { type: "reasoning" }> | null = null;
  let tools: Extract<ResponseSegment, { type: "tools" }> | null = null;
  let narrated = false;
  const visible = events.filter((entry) => entry.kind !== "echo");
  for (let index = 0; index < visible.length; index++) {
    const entry = visible[index];
    if (entry.kind === "progress") {
      segments.push({ type: "narration", key: `narration-${entry.id}`, entry });
      reasoning = null;
      tools = null;
      narrated = true;
    } else if (entry.kind === "thinking") {
      if (!reasoning) {
        reasoning = { type: "reasoning", key: `reasoning-${entry.id}`, entries: [] };
        segments.push(reasoning);
      }
      reasoning.entries.push(entry);
    } else if (entry.kind === "tool_call" || entry.kind === "tool_result") {
      if (!tools) {
        if (!narrated && entry.kind === "tool_call") segments.push({ type: "inferred", key: `inferred-${entry.id}`, call: entry });
        tools = { type: "tools", key: `tools-${entry.id}`, steps: [] };
        segments.push(tools);
      }
      const next = visible[index + 1];
      const result = entry.kind === "tool_call" && next?.kind === "tool_result" ? next : undefined;
      tools.steps.push({ call: entry, result });
      if (result) index++;
    } else {
      segments.push({ type: "entry", key: `entry-${entry.id}`, entry });
      reasoning = null;
      tools = null;
      narrated = false;
    }
  }
  return segments;
}
