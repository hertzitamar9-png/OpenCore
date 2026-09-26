import { describe, expect, it } from "vitest";
import { profileLabel, selectableModelProfiles } from "./ModelProfiles";

describe("model profile context claims", () => {
  it("labels the 1M YaRN window as extended and shows its trained context", () => {
    const profile = selectableModelProfiles.find(({ id }) => id === "native1m");

    expect(profileLabel("native1m")).toBe("1M extended");
    expect(profile).toMatchObject({
      label: "1M extended",
      description: "1,000,000-token YaRN window · trained context 262,144",
    });
  });
});
