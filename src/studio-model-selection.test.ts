import {expect, it} from 'vitest';
import {selectStudioModel} from './studio-model-selection';

const model = (id: string, category = 'video', installed = false, runtimeConnected = false) => ({id, category, installed, runtimeConnected});
it.each([
  ['video', 'ltx-25-distilled-bf16'], ['image', 'flux-2-klein-4b'], ['3d', 'trellis-2-4b'],
  ['2d-animation', 'wan-animate-2-distilled'], ['tts', 'tts-qwen3-customvoice-1-7b'],
  ['voice-cloning', 'voice-cloning-qwen3-base-1-7b'], ['ocr', 'ocr-glm-ocr'],
  ['omni', 'omni-voxtral-mini-4b-realtime-2602'], ['policy', 'policy-gr00t-n1-7-3b'],
])('prefers the verified release for %s regardless of catalog order', (category, preferred) => {
  expect(selectStudioModel([model('old', category), model(preferred, category)], category)).toBe(preferred);
});
it.each([[true, false], [false, true]])('keeps an existing installed=%s connected=%s model ahead of a release default', (installed, connected) => {
  expect(selectStudioModel([model('ltx-25-distilled-bf16'), model('existing', 'video', installed, connected)], 'video')).toBe('existing');
});
it('retains a deliberate current selection when the list changes', () => {
  expect(selectStudioModel([model('ltx-25-distilled-bf16'), model('installed', 'video', true), model('chosen')], 'video', 'chosen')).toBe('chosen');
});
it('ignores removed selections and models in another category', () => {
  expect(selectStudioModel([model('ltx-25-distilled-bf16', 'video', true), model('fallback', 'image')], 'image', 'removed')).toBe('fallback');
  expect(selectStudioModel([], 'video')).toBe('');
});
