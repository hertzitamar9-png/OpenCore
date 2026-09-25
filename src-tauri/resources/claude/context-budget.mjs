const integer = (value, fallback) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
};

export function deriveContextBudget(config = {}) {
  const contextWindowTokens = Math.max(8_192, integer(config.contextWindowTokens, 32_768));
  const requestedCompactAtTokens = Math.max(1_024, integer(config.compactAtTokens, 200_000));
  const headroomTokens = Math.min(
    Math.floor(contextWindowTokens * 0.3),
    Math.max(10_240, Math.floor(contextWindowTokens * 0.2)),
  );
  const safeCompactCeiling = Math.max(1_024, contextWindowTokens - headroomTokens);
  const autoCompactThresholdTokens = Math.min(requestedCompactAtTokens, safeCompactCeiling);
  // Allocate most, but not all, of the headroom between the user's compact
  // point and the hard model limit across a parallel batch of tool results.
  // This can exceed Claude Code's default MCP result allowance for large windows.
  const remainingHeadroomTokens = Math.max(1_024, contextWindowTokens - autoCompactThresholdTokens);
  const maxConcurrentToolUses = Math.max(1, Math.min(10, Math.floor(remainingHeadroomTokens / 4_096)));
  const toolOutputTokens = Math.max(1_024, Math.floor(remainingHeadroomTokens * 0.75 / maxConcurrentToolUses));
  const bashOutputLength = Math.min(150_000, Math.max(4_000, toolOutputTokens * 4));

  return {
    contextWindowTokens,
    requestedCompactAtTokens,
    autoCompactThresholdTokens,
    remainingHeadroomTokens,
    toolOutputTokens,
    bashOutputLength,
    maxConcurrentToolUses,
  };
}
