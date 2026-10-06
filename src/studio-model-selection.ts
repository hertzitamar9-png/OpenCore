type StudioCandidate = {id: string; category?: string; installed: boolean; runtimeConnected?: boolean};

// Defaults identify audited releases; they do not imply runtime readiness or hardware fit.
const releaseDefaults: Record<string, string> = {
  video: 'ltx-25-distilled-bf16',
  image: 'flux-2-klein-4b',
  '3d': 'trellis-2-4b',
  '2d-animation': 'wan-animate-2-distilled',
  tts: 'tts-qwen3-customvoice-1-7b',
  'voice-cloning': 'voice-cloning-qwen3-base-1-7b',
  ocr: 'ocr-glm-ocr',
  omni: 'omni-voxtral-mini-4b-realtime-2602',
  policy: 'policy-gr00t-n1-7-3b',
};

export function selectStudioModel(models: readonly StudioCandidate[], category: string, currentModelId = ''): string {
  const candidates = models.filter(model => model.category === category);
  if (candidates.some(model => model.id === currentModelId)) return currentModelId;
  return candidates.find(model => model.installed || model.runtimeConnected)?.id
    || candidates.find(model => model.id === releaseDefaults[category])?.id
    || candidates[0]?.id || '';
}
