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
      expect(proof?.modelCardSha256).toMatch(/^[a-f\d]{64}$/);
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
});
