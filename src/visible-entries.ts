import type { TimelineEntry } from "./types";

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
