import { describe, expect, it } from "vitest";
import { profileLabel, profileUsesEcho, selectableModelProfiles } from "./ModelProfiles";

describe("model profile context claims", () => {
  it('uses catalog memory mode for profiles whose IDs do not contain echo', () => {
    expect(profileUsesEcho('native1m')).toBe(true);
    expect(profileUsesEcho('neohorse-1-9b')).toBe(true);
    expect(profileUsesEcho('underdog-saluki-27b')).toBe(true);
    expect(profileUsesEcho('ista-qwen38-27b')).toBe(true);
    expect(profileUsesEcho('underdog-woof-4b-11')).toBe(true);
    expect(profileUsesEcho('underdog-saluki-27b-native')).toBe(false);
    expect(profileUsesEcho('echo-native')).toBe(false);
    expect(profileUsesEcho('stopped')).toBe(false);
  });
  it("exposes the HumanEval GGUF models as selectable installed profiles", () => {
    const expected = [
      "oxcoder-9b", "nim-2-coder-7b", "ternary-bonsai-2-27b", "mimo-distill-qwen-9b",
      "frognano-4b", "qwen38-distill-9b", "triumvirate-9b-coder", "orion-agentic-9b",
      "zenith-9b-codecore", "neohorse-1-9b", "boomslang-3b", "tiel-inspired-coder-9b",
      "gmcoder", "ornith-1-5-9b-mtp",
    ];
    const actual = new Set<string>(selectableModelProfiles.map(({ id }) => id));

    expect(expected.filter((id) => !actual.has(id))).toEqual([]);
  });

  it("labels the existing K2 and Nanbeige candidate selector DuoCore", () => {
    const profile = selectableModelProfiles.find(({ id }) => id === "doucode");

    expect(profileLabel("doucode")).toBe("DuoCore · ECHO");
    expect(profile).toMatchObject({
      label: "DuoCore · ECHO",
      description: "K2 + Nanbeige · competing drafts, one selected answer · ECHO archive",
    });
  });

  it("labels the 1M YaRN window as extended and shows its trained context", () => {
    const profile = selectableModelProfiles.find(({ id }) => id === "native1m");

    expect(profileLabel("native1m")).toBe("1M extended · ECHO");
    expect(profile).toMatchObject({
      label: "1M extended · ECHO",
      description: "1,000,000-token YaRN window · ECHO archive · trained context 262,144",
    });
  });

  it("keeps ECHO retrieval separate from active KV decoding in the model picker", () => {
    expect(selectableModelProfiles.find(({ id }) => id === "dualcore-echo")?.description).toContain("ECHO archive");
    expect(selectableModelProfiles.find(({ id }) => id === "dualcore-echo")?.description).toContain("incremental KV");
    expect(selectableModelProfiles.find(({ id }) => id === "fusioncore-echo")?.description).toContain("ECHO archive");
    expect(selectableModelProfiles.find(({ id }) => id === "fusioncore-echo")?.description).toContain("incremental KV");
  });

  it("offers native context profiles separately from ECHO-backed profiles", () => {
    expect(selectableModelProfiles.find(({ id }) => id === "dualcore-kv")?.description).toContain("native context");
    expect(selectableModelProfiles.find(({ id }) => id === "fusioncore-kv")?.description).toContain("native context");
  });

});
