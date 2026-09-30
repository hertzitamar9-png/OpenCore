import { expect, it } from "vitest";
import type { TimelineEntry } from "./types";
import { removePersistedOptimisticDuplicates } from "./visible-entries";

function entry(id: number, timestamp: string, submissionId: string): TimelineEntry {
  return {
    id, conversationId: "chat", timestamp, kind: "message", role: "user", source: "OpenCore",
    title: "You", content: "What is in this photo?", metadata: { submissionId },
  };
}

it("hides the pending copy when the persisted submission arrives more than 30 seconds later", () => {
  const pending = entry(-1, "2026-09-28T00:00:00Z", "submission-a");
  const saved = entry(12, "2026-09-28T00:00:45Z", "submission-a");

  expect(removePersistedOptimisticDuplicates([saved], [pending])).toEqual([]);
});

it("keeps identical text when it belongs to a different submission", () => {
  const pending = entry(-1, "2026-09-28T00:00:00Z", "submission-a");
  const saved = entry(12, "2026-09-28T00:00:45Z", "submission-b");

  expect(removePersistedOptimisticDuplicates([saved], [pending])).toEqual([pending]);
});
