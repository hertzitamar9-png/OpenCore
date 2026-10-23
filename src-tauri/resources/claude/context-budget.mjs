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
  const autoCompactWindowTokens = Math.min(contextWindowTokens, 1_000_000);
  const autoCompactThresholdTokens = Math.min(requestedCompactAtTokens, safeCompactCeiling, autoCompactWindowTokens);
  // Claude Code treats AUTO_COMPACT_WINDOW as the capacity used to calculate
  // compaction, not as the desired trigger point. Set that to the real model
  // window and express the user's earlier trigger as a percentage.
  const autoCompactPercentOverride = Math.max(1, Math.min(100,
    Math.floor(autoCompactThresholdTokens / autoCompactWindowTokens * 100)));
  // Allocate most, but not all, of the headroom between the user's compact
  // point and the hard model limit across a parallel batch of tool results.
  // This can exceed Claude Code's default MCP result allowance for large windows.
  const remainingHeadroomTokens = Math.max(1_024, contextWindowTokens - autoCompactThresholdTokens);
  // Unknown gateway model IDs default to a 32K output cap in Claude Code. On a
  // small local context that leaves no input room for auto-compaction, so
  // reserve half the headroom for the answer and half for compaction/tool slack.
  const maxOutputTokens = Math.min(32_000, Math.max(1_024, Math.floor(remainingHeadroomTokens / 2)));
  const maxConcurrentToolUses = Math.max(1, Math.min(10, Math.floor(remainingHeadroomTokens / 4_096)));
  const toolOutputTokens = Math.max(1_024, Math.floor(remainingHeadroomTokens * 0.75 / maxConcurrentToolUses));
  const bashOutputLength = Math.min(150_000, Math.max(4_000, toolOutputTokens * 4));

  return {
    contextWindowTokens,
    requestedCompactAtTokens,
    autoCompactThresholdTokens,
    autoCompactWindowTokens,
    autoCompactPercentOverride,
    maxOutputTokens,
    remainingHeadroomTokens,
    toolOutputTokens,
    bashOutputLength,
    maxConcurrentToolUses,
  };
}

export function contextBudgetEnvironment(budget) {
  const env = {
    CLAUDE_CODE_MAX_CONTEXT_TOKENS: String(budget.contextWindowTokens),
    CLAUDE_CODE_MAX_OUTPUT_TOKENS: String(budget.maxOutputTokens),
    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: String(budget.autoCompactPercentOverride),
  };
  // Claude Code accepts this override only from 100K upward. Below that, the
  // corrected model context window itself remains the compaction window.
  if (budget.autoCompactWindowTokens >= 100_000) {
    env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = String(budget.autoCompactWindowTokens);
  }
  return env;
}
