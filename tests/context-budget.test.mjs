import assert from 'node:assert/strict';
import test from 'node:test';
import { contextBudgetEnvironment, deriveContextBudget } from '../src-tauri/resources/claude/context-budget.mjs';

test('Claude Code receives the full context window and a separate requested compact percentage', () => {
  const budget = deriveContextBudget({ contextWindowTokens: 32_768, compactAtTokens: 200_000 });
  assert.deepEqual(contextBudgetEnvironment(budget), {
    CLAUDE_CODE_MAX_CONTEXT_TOKENS: '32768',
    CLAUDE_CODE_MAX_OUTPUT_TOKENS: '4915',
    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: '70',
  });
});

test('a 32K model scales output and concurrency to its remaining context headroom', () => {
  assert.deepEqual(deriveContextBudget({ contextWindowTokens: 32_768, compactAtTokens: 200_000 }), {
    contextWindowTokens: 32_768,
    requestedCompactAtTokens: 200_000,
    autoCompactThresholdTokens: 22_938,
    autoCompactWindowTokens: 32_768,
    autoCompactPercentOverride: 70,
    maxOutputTokens: 4_915,
    remainingHeadroomTokens: 9_830,
    toolOutputTokens: 3_686,
    bashOutputLength: 14_744,
    maxConcurrentToolUses: 2,
  });
});

test('a larger model honors the requested 200K threshold', () => {
  const budget = deriveContextBudget({ contextWindowTokens: 262_144, compactAtTokens: 200_000 });
  assert.equal(budget.autoCompactThresholdTokens, 200_000);
  assert.equal(budget.autoCompactWindowTokens, 262_144);
  assert.equal(budget.autoCompactPercentOverride, 76);
  assert.equal(budget.maxOutputTokens, 31_072);
  assert.deepEqual(contextBudgetEnvironment(budget), {
    CLAUDE_CODE_MAX_CONTEXT_TOKENS: '262144',
    CLAUDE_CODE_MAX_OUTPUT_TOKENS: '31072',
    CLAUDE_CODE_AUTO_COMPACT_WINDOW: '262144',
    CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: '76',
  });
  assert.equal(budget.remainingHeadroomTokens, 62_144);
  assert.equal(budget.toolOutputTokens, 4_660);
  assert.equal(budget.bashOutputLength, 18_640);
  assert.equal(budget.maxConcurrentToolUses, 10);
});

test('a million-token model can exceed Claude Code MCP default when context allows', () => {
  const budget = deriveContextBudget({ contextWindowTokens: 1_000_000, compactAtTokens: 200_000 });
  assert.equal(budget.toolOutputTokens, 60_000);
  assert.equal(budget.maxOutputTokens, 32_000);
  assert.equal(budget.maxConcurrentToolUses, 10);
  assert.equal(budget.bashOutputLength, 150_000);
});

test('a lower user threshold is retained', () => {
  const budget = deriveContextBudget({ contextWindowTokens: 32_768, compactAtTokens: 12_000 });
  assert.equal(budget.autoCompactThresholdTokens, 12_000);
});
