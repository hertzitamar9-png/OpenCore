import { describe, expect, it } from 'vitest';
import { defaultLearningConfig, learningStartRequest, formatExactTime, trainingModePrompt } from './learning-config';

describe('Learning Studio configuration', () => {
  it('keeps BF16 selected and bounded training budgets in a manual request', () => {
    const request = learningStartRequest({modelPath: 'C:/models/base', mode: 'manual', config: {...defaultLearningConfig}, verifiedOnly: true});
    expect(request.config.precision).toBe('bf16-lora');
    expect(request.config.maxSteps).toBeGreaterThan(0);
    expect(request.config.maxMinutes).toBeGreaterThan(0);
    expect(request.verifiedOnly).toBe(true);
  });
  it('requires an assistant chat before automatic tuning can start', () => {
    expect(() => learningStartRequest({modelPath: 'C:/models/base', mode: 'auto', config: {...defaultLearningConfig}, verifiedOnly: true})).toThrow(/assistant/i);
  });
  it('keeps manual configuration distinct from AI configuration and automatic execution', () => {
    expect(trainingModePrompt('configure', 'Learn my style', 30)).toContain('Do not start training');
    expect(trainingModePrompt('auto', 'Learn my style', 30)).toContain('checkpoint');
    expect(trainingModePrompt('auto', 'Learn my style', 30)).toContain('30');
  });
  it('shows the exact second and four digit year', () => {
    expect(formatExactTime('2026-10-07T13:14:59Z')).toMatch(/2026/);
    expect(formatExactTime('2026-10-07T13:14:59Z')).toMatch(/:59/);
  });
});
