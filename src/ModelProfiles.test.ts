import { describe, expect, it } from "vitest";
import { profileLabel, selectableModelProfiles } from "./ModelProfiles";

describe("model profile context claims", () => {
  it("labels the existing K2 and Nanbeige candidate selector DuoCore", () => {
    const profile = selectableModelProfiles.find(({ id }) => id === "doucode");

    expect(profileLabel("doucode")).toBe("DuoCore");
    expect(profile).toMatchObject({
      label: "DuoCore",
      description: "K2 + Nanbeige · competing drafts, one selected answer",
    });
  });

  it("labels the 1M YaRN window as extended and shows its trained context", () => {
    const profile = selectableModelProfiles.find(({ id }) => id === "native1m");

    expect(profileLabel("native1m")).toBe("1M extended");
    expect(profile).toMatchObject({
      label: "1M extended",
      description: "1,000,000-token YaRN window · trained context 262,144",
    });
  });

});
