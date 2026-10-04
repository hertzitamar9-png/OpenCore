import { describe, expect, it } from "vitest";
import type { InstalledModel } from "./api";
import { groupModelVariants, estimateGgufVramRange, filterGroupsByMemoryMode, modelMemoryMode } from "./model-variants";

const model = (id: string, variantOf?: string, downloadBytes = 5_000_000_000): InstalledModel => ({
  id, label: id, description: "GGUF model", precision: "Q4_K_M", contextTokens: 16_384,
  license: "test", experimental: false, note: "test", selectable: true, installed: false,
  externalManaged: false, downloadBytes, totalBytes: downloadBytes, category: "text", backend: "gguf", variantOf,
});

describe("quantized model variants", () => {
  it("groups pinned quant variants beneath one model family", () => {
    const groups = groupModelVariants([model("coder"), model("coder-q5", "coder"), model("other")]);
    expect(groups.map(group => [group.id, group.variants.map(variant => variant.id)])).toEqual([
      ["coder", ["coder", "coder-q5"]], ["other", ["other"]],
    ]);
  });

  it("estimates full-GPU VRAM as a range above the download size", () => {
    const estimate = estimateGgufVramRange(5_000_000_000, 16_384);
    expect(estimate.minBytes).toBeGreaterThan(5_500_000_000);
    expect(estimate.maxBytes).toBeGreaterThan(estimate.minBytes);
    expect(estimate.maxBytes - estimate.minBytes).toBeGreaterThan(500_000_000);
  });

  it("keeps native and ECHO profiles together without treating them as separate quantizations", () => {
    const root = model("coder");
    root.memoryMode = "echo";
    const native = model("coder-native", "coder");
    native.memoryMode = "native";
    const groups = groupModelVariants([root, native]);

    expect(groups).toHaveLength(1);
    expect(filterGroupsByMemoryMode(groups, "native")[0].variants.map(model => model.id)).toEqual(["coder-native"]);
    expect(filterGroupsByMemoryMode(groups, "echo")[0].variants.map(model => model.id)).toEqual(["coder"]);
    expect(modelMemoryMode(native)).toBe("native");
    expect(modelMemoryMode(root)).toBe("echo");
  });

  it("does not inflate expected GPU VRAM based on CPU-resident KV context", () => {
    expect(estimateGgufVramRange(5_000_000_000, 1_000_000)).toEqual(estimateGgufVramRange(5_000_000_000, 16_384));
  });

  it("accounts for multiple full weight copies in the same runtime profile", () => {
    expect(estimateGgufVramRange(3_000_000_000, 16_384, 2).minBytes).toBe(6_800_000_000);
  });
});
