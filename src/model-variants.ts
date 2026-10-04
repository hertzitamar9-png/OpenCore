import type { InstalledModel } from "./api";

export interface ModelVariantGroup {
  id: string;
  model: InstalledModel;
  variants: InstalledModel[];
}

export type ModelMemoryMode = "all" | "native" | "echo";

export function modelMemoryMode(model: InstalledModel): Exclude<ModelMemoryMode, "all"> {
  return model.memoryMode || (model.category === "text" && model.backend === "gguf" ? "echo" : "native");
}

function profileFamily(id: string): string {
  if (id === "nanbeige-bf16-echo" || id.startsWith("nanbeige-bf16-echo-")) return id.replace("nanbeige-bf16-echo", "nanbeige-bf16");
  if (id === "dualcore-echo") return "dualcore-kv";
  if (id === "fusioncore-echo") return "fusioncore-kv";
  if (id.endsWith("-native") && ["echo-native", "native1m-native", "doucode-native"].includes(id)) return id.slice(0, -"-native".length);
  return id;
}

export function groupModelVariants(models: InstalledModel[]): ModelVariantGroup[] {
  const groups = new Map<string, ModelVariantGroup>();
  for (const model of models) {
    const id = profileFamily(model.variantOf || model.id);
    const group = groups.get(id);
    if (group) group.variants.push(model);
    else groups.set(id, { id, model: model.variantOf ? models.find(item => item.id === id) || model : model, variants: [model] });
  }
  return [...groups.values()].map(group => ({
    ...group,
    variants: group.variants.sort((a, b) => a.id === group.id ? -1 : b.id === group.id ? 1 : a.precision.localeCompare(b.precision)),
  }));
}

export function filterGroupsByMemoryMode(groups: ModelVariantGroup[], mode: ModelMemoryMode): ModelVariantGroup[] {
  if (mode === "all") return groups;
  return groups.flatMap(group => {
    const variants = group.variants.filter(model => modelMemoryMode(model) === mode);
    return variants.length ? [{ ...group, variants }] : [];
  });
}

export function estimateGgufVramRange(weightBytes: number, contextTokens: number, weightMultiplier = 1): { minBytes: number; maxBytes: number } {
  void contextTokens; // These profiles keep KV/state in system RAM; context length must not inflate GPU weight estimates.
  const weights = weightBytes * weightMultiplier;
  return {
    minBytes: Math.ceil(weights + 800_000_000),
    maxBytes: Math.ceil(weights + 1_500_000_000),
  };
}
