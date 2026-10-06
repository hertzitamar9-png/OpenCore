import type { InstalledModel } from "./api";

export interface ModelVariantGroup {
  id: string;
  model: InstalledModel;
  variants: InstalledModel[];
  aliases: Record<string, string>;
}

function deliveryIdentity(model: InstalledModel): string {
  // A missing artifact list is not evidence that two external packages are identical.
  if (!model.artifactIdentity) return JSON.stringify(["profile", model.id]);
  return JSON.stringify([model.artifactIdentity, modelMemoryMode(model), model.backend, model.precision,
    model.contextTokens, model.vramWeightMultiplier || 1, model.runtimeModelPath, model.visionProjectorPath,
    model.speechLanguage, model.runtimePrecision, model.selectable, model.installable !== false, model.runtimeReady !== false]);
}

export function matchingModelVariant(group: ModelVariantGroup, source: InstalledModel | undefined): InstalledModel | undefined {
  if (!source) return undefined;
  const exact = group.variants.find(model => model.id === (group.aliases[source.id] || source.id));
  if (exact) return exact;
  // When a mode filter hides the remembered profile, follow only its matching weights.
  const counterparts = group.variants.filter(model => model.precision === source.precision && model.backend === source.backend &&
    model.contextTokens === source.contextTokens && (model.vramWeightMultiplier || 1) === (source.vramWeightMultiplier || 1) &&
    (source.artifactIdentity ? model.artifactIdentity === source.artifactIdentity :
      profileFamily(model.id).replace(/-native$/, "") === profileFamily(source.id).replace(/-native$/, "")));
  return counterparts.length === 1 ? counterparts[0] : undefined;
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
  const byId = new Map(models.map(model => [model.id, model]));
  for (const model of models) {
    let id = profileFamily(model.variantOf || model.id);
    const seen = new Set([model.id]);
    while (!seen.has(id)) {
      seen.add(id);
      const parent = byId.get(id);
      if (!parent?.variantOf) break;
      id = profileFamily(parent.variantOf);
    }
    const group = groups.get(id);
    if (group) group.variants.push(model);
    else groups.set(id, { id, model: byId.get(id) || model, variants: [model], aliases: Object.create(null) as Record<string, string> });
  }
  return [...groups.values()].map(group => {
    const deliveries = new Map<string, InstalledModel>();
    const availability = (model: InstalledModel) => Number(model.installed) * 2 + Number(model.externalManaged);
    for (const model of group.variants) {
      const key = deliveryIdentity(model);
      const existing = deliveries.get(key);
      if (!existing || availability(model) > availability(existing)) deliveries.set(key, model);
    }
    for (const model of group.variants) group.aliases[model.id] = deliveries.get(deliveryIdentity(model))!.id;
    return { ...group, variants: [...deliveries.values()].sort((a, b) =>
      a.id === group.id ? -1 : b.id === group.id ? 1 : a.precision.localeCompare(b.precision)) };
  });
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
