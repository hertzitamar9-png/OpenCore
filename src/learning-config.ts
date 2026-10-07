export type LearningMode = 'manual' | 'configure' | 'auto';
export interface LearningConfig {
  method: 'sft' | 'dpo'; precision: 'bf16-lora' | 'qlora-4bit'; epochs: number; maxSteps: number;
  learningRate: number; loraRank: number; loraAlpha: number; loraDropout: number;
  batchSize: number; gradientAccumulation: number; maxSeqLength: number; optimizer: string;
  checkpointEvery: number; seed: number; maxMinutes: number; maxDiskBytes: number;
  minimumImprovement: number; minEvaluationSamples: number; maxRegression: number; dpoBeta: number;
}
export const defaultLearningConfig: LearningConfig = {
  method: 'sft', precision: 'bf16-lora', epochs: 1, maxSteps: 100, learningRate: 0.0002,
  loraRank: 16, loraAlpha: 32, loraDropout: 0, batchSize: 1, gradientAccumulation: 4,
  maxSeqLength: 1024, optimizer: 'adamw_torch', checkpointEvery: 25, seed: 3407,
  maxMinutes: 30, maxDiskBytes: 10 * 1024 ** 3, minimumImprovement: 0.01,
  minEvaluationSamples: 8, maxRegression: 0, dpoBeta: 0.1,
};
export interface LearningStart {
  modelPath: string; mode: LearningMode; config: LearningConfig; verifiedOnly: boolean;
  conversationId?: string; name?: string; recordIds?: string[];
}
export function learningStartRequest(input: LearningStart): LearningStart & {action: 'start'} {
  if (input.mode === 'auto' && !input.conversationId) throw new Error('Open the learning assistant before starting automatic tuning.');
  if (!input.modelPath.trim()) throw new Error('Choose the original local Hugging Face checkpoint.');
  if (!(input.config.maxSteps > 0 && input.config.maxMinutes > 0 && input.config.maxDiskBytes > 0)) throw new Error('Training needs positive step, time and disk limits.');
  return {...input, config: {...input.config}, action: 'start'};
}
export function formatExactTime(value?: string | null): string {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(undefined, {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
}
export function trainingModePrompt(mode: Exclude<LearningMode,'manual'>, goal: string, minutes: number): string {
  const purpose = goal.trim() || 'Help me improve this model using the eligible evidence in Learning Studio.';
  return `${purpose}\n\nUse Learning Studio (learning_use) to inspect the raw records, source quality, model architecture and available hardware. My time budget is ${minutes} minutes. Ask about goals or constraints only when needed; choose the technical numbers from measured hardware/model/data. Preserve BF16 unless I explicitly choose QLoRA. Save the exact configuration and explain the evidence and limits. ${mode === 'configure' ? 'AI configuration only: Do not start training. Fill the studio configuration for me to review and run.' : 'Run automatic tuning end to end with mode=auto, saved checkpoint reviews and final evaluation. Release inference while training, review each checkpoint with measured loss and evidence, and report the final accepted/rejected/failed result and exact reasons. Keep rejected artifacts and the original model.'}`;
}
