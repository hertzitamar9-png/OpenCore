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

  it("collapses only identical artifacts and delivery settings", () => {
    const base = { ...model("image"), category: "image", backend: "external", artifactIdentity: "pinned-file-a", memoryMode: "native" as const };
    const alias = { ...base, id: "image-copy", variantOf: base.id, installed: true };
    const otherFile = { ...base, id: "other-file", variantOf: base.id, artifactIdentity: "pinned-file-b" };
    const otherBackend = { ...base, id: "other-backend", variantOf: base.id, backend: "diffusers" };
    const echo = { ...base, id: "image-echo", variantOf: base.id, memoryMode: "echo" as const };
    const groups = groupModelVariants([base, alias, otherFile, otherBackend, echo]);
    expect(groups[0].variants.map(item => item.id)).toEqual([alias.id, otherFile.id, otherBackend.id, echo.id]);
    expect(groups[0].variants[0].installed).toBe(true);
  });

  it("preserves distinct setup packages with the same precision and no download files", () => {
    const base = { ...model("video", undefined, 0), precision: "BF16", backend: "external", installable: false };
    const dev = { ...base, id: "video-dev", variantOf: base.id };
    expect(groupModelVariants([base, dev])[0].variants.map(item => item.id)).toEqual([base.id, dev.id]);
  });

  it("keeps quantized image descendants in the full pipeline family", () => {
    const base = model("image");
    const gguf = model("image-gguf", base.id);
    const q4 = model("image-gguf-q4", gguf.id);
    expect(groupModelVariants([base, gguf, q4]).map(group => [group.id, group.variants.length])).toEqual([[base.id, 3]]);
  });

  it("does not merge different contexts or weight copies sharing one artifact", () => {
    const base = { ...model("coder"), artifactIdentity: "file" };
    const longer = { ...base, id: "coder-long", variantOf: base.id, contextTokens: 1_000_000 };
    const multi = { ...base, id: "coder-two", variantOf: base.id, vramWeightMultiplier: 2 };
    expect(groupModelVariants([base, longer, multi])[0].variants).toHaveLength(3);
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
