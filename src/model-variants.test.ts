import { describe, expect, it } from "vitest";
import type { InstalledModel } from "./api";
import { groupModelVariants, estimateGgufVramRange } from "./model-variants";

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
    expect(estimate.minBytes).toBeGreaterThan(5_000_000_000);
    expect(estimate.maxBytes).toBeGreaterThan(estimate.minBytes);
    expect(estimate.maxBytes - estimate.minBytes).toBeGreaterThan(1_000_000_000);
  });
});
