import { describe, expect, it } from 'vitest';
import catalog from '../src-tauri/resources/model-catalog.json';
import evidence from '../src-tauri/resources/model-catalog-evidence.json';

describe('media catalog provenance and availability', () => {
  it.each(['video', 'tts', 'voice-cloning', 'ocr', 'omni', 'policy'])('provides ten distinct publisher identities for %s without inventing install support', category => {
    const models = catalog.models.filter(model => model.category === category);
    expect(models.length).toBeGreaterThanOrEqual(10);
    expect(new Set(models.map(model => 'sourceUrl' in model ? model.sourceUrl : '')).size).toBeGreaterThanOrEqual(10);
    for (const model of models) {
      expect(model).toMatchObject({ runtimeReady: false, selectable: false, backend: 'external' });
      expect('sourceUrl' in model && model.sourceUrl).toMatch(/^https:\/\/huggingface\.co\/[^/]+\/[^/]+\/tree\/[a-f\d]{40}$/);
      expect('setupUrl' in model && model.setupUrl).toMatch(/^https:\/\//);
      expect(model.license).not.toBe('');
      const proof = evidence.entries.find(entry => entry.modelId === model.id);
      expect(proof?.revision).toMatch(/^[a-f\d]{40}$/);
      if ('modelCardAccess' in (proof || {}) && proof?.modelCardAccess === 'gated-raw-card-unavailable') {
        expect(proof?.gated).toBeTruthy();
        expect(proof?.modelCardSha256).toBeNull();
        expect(proof?.apiMetadataSha256).toMatch(/^[a-f\d]{64}$/);
      } else expect(proof?.modelCardSha256).toMatch(/^[a-f\d]{64}$/);
      if ('installable' in model && model.installable) {
        expect(model.artifacts.length).toBeGreaterThan(0);
        expect('weightArtifacts' in model && model.weightArtifacts?.length).toBeGreaterThan(0);
        const files = catalog.artifacts.filter(file => model.artifacts.includes(file.id));
        expect(files.find(file => file.filename === 'README.md')?.sha256).toBe(proof?.modelCardSha256);
        expect(files.every(file => file.revision === proof?.revision && file.repo === proof.repo)).toBe(true);
        expect(files.every(file => /^[a-f\d]{64}$/.test(file.sha256) && Number.isSafeInteger(file.bytes) && file.bytes > 0)).toBe(true);
        for (const index of proof?.indexChecks || []) {
          const folder = index.filename.slice(0, index.filename.lastIndexOf('/') + 1);
          for (const shard of index.shards) expect(files.some(file => file.filename === folder + shard)).toBe(true);
        }
      } else expect(model.artifacts).toEqual([]);
    }
  });
  it('keeps artifact identifiers unique and every downloadable model bound to published files', () => {
    const ids = catalog.artifacts.map(file => file.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(catalog.models.map(model => model.id)).size).toBe(catalog.models.length);
    for (const model of catalog.models) {
      for (const id of model.artifacts) expect(ids).toContain(id);
    }
  });
  it('offers the official LTX 2.5 pack without rewriting older installed identities', () => {
    const pack = catalog.models.find(model => model.id === 'ltx-25-distilled-bf16');
    expect(pack).toMatchObject({category: 'video', precision: 'BF16', installable: false, runtimeReady: false, backend: 'external'});
    expect(pack?.sourceUrl).toBe('https://huggingface.co/Lightricks/LTX-2.5/tree/2356ce76915d6c48d313d7e8b25900e1dd3abaa8');
    expect(catalog.models.find(model => model.id === 'ltx-video')?.sourceUrl).toBe('https://huggingface.co/Lightricks/LTX-Video/tree/8984fa25007f376c1a299016d0957a37a2f797bb');
    expect(catalog.models.find(model => model.id === 'ltx-2')?.sourceUrl).toBe('https://huggingface.co/Lightricks/LTX-2/tree/dfcc2108383fe1aaa0584bdf55d368a4bdadd90c');
    const proof = evidence.entries.find(entry => entry.modelId === pack?.id);
    expect(proof?.publishedFiles).toContainEqual({filename: 'diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors', bytes: 42018190584, sha256: '31eb3cad89b9e54e99dd3baf286f70825ac4f6c660a70d9184d895be76d7bff4'});
  });
  it.each(['flux-2-klein-4b', 'z-image-turbo', 'trellis-2-4b', 'wan-animate-2-distilled', 'tts-qwen3-customvoice-1-7b', 'voice-cloning-qwen3-base-1-7b', 'ocr-glm-ocr', 'ocr-paddleocr-vl-1-6', 'policy-gr00t-n1-7-3b', 'omni-voxtral-mini-4b-realtime-2602', 'acestep-15-xl-turbo'])('offers verified released checkpoints for %s without claiming a built-in adapter', id => {
    const model = catalog.models.find(candidate => candidate.id === id);
    expect(model).toMatchObject({selectable: false, runtimeReady: false, backend: 'external', installable: true});
    expect(model?.artifacts.length).toBeGreaterThan(0);
    const files = catalog.artifacts.filter(file => model?.artifacts.includes(file.id));
    expect(files.some(file => /\.(safetensors|ckpt|pt|pth|bin)$/.test(file.filename))).toBe(true);
    const proof = evidence.entries.find(entry => entry.modelId === id);
    expect(files.every(file => proof?.publishedFiles?.some(published => published.filename === file.filename && published.bytes === file.bytes && published.sha256 === file.sha256))).toBe(true);
  });
});
