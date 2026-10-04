import type { InstalledModel } from "./api";

export interface ModelVariantGroup {
  id: string;
  model: InstalledModel;
  variants: InstalledModel[];
}

export function groupModelVariants(models: InstalledModel[]): ModelVariantGroup[] {
  const groups = new Map<string, ModelVariantGroup>();
  for (const model of models) {
    const id = model.variantOf || model.id;
    const group = groups.get(id);
    if (group) group.variants.push(model);
    else groups.set(id, { id, model: model.variantOf ? models.find(item => item.id === id) || model : model, variants: [model] });
  }
  return [...groups.values()].map(group => ({
    ...group,
    variants: group.variants.sort((a, b) => a.id === group.id ? -1 : b.id === group.id ? 1 : a.precision.localeCompare(b.precision)),
  }));
}

export function estimateGgufVramRange(downloadBytes: number, contextTokens: number): { minBytes: number; maxBytes: number } {
  const contextScale = Math.max(0.5, contextTokens / 16_384);
  return {
    minBytes: Math.ceil(downloadBytes + 800_000_000 + 500_000_000 * contextScale),
    maxBytes: Math.ceil(downloadBytes + 1_500_000_000 + 1_500_000_000 * contextScale),
  };
}
