import type { TimelineEntry } from "./types";

// Preserve the array reference when a poll contains no edits. This avoids
// regrouping turns and rendering Markdown across the whole transcript again.
export function retainUnchangedTimeline(previous: TimelineEntry[], next: TimelineEntry[]): TimelineEntry[] {
  return previous.length === next.length && previous.every((entry, index) => {
    const other = next[index];
    return entry.id === other.id && entry.conversationId === other.conversationId &&
      entry.timestamp === other.timestamp && entry.kind === other.kind && entry.role === other.role &&
      entry.source === other.source && entry.title === other.title && entry.content === other.content &&
      JSON.stringify(entry.metadata) === JSON.stringify(other.metadata);
  }) ? previous : next;
}

export function removePersistedOptimisticDuplicates(
  persisted: TimelineEntry[],
  optimistic: TimelineEntry[],
): TimelineEntry[] {
  const persistedSubmissionIds = new Set(
    persisted
      .map((entry) => entry.metadata.submissionId)
      .filter((value): value is string => typeof value === "string" && value.length > 0),
  );
  return optimistic.filter((entry) => {
    const submissionId = entry.metadata.submissionId;
    return typeof submissionId !== "string" || !persistedSubmissionIds.has(submissionId);
  });
}
