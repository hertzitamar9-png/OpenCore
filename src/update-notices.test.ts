import { describe, expect, it } from "vitest";
import { updateNoticeMessage, updateNoticeTimeout } from "./update-notices";

describe("automatic update notices", () => {
  it("shows the verified installed version after a successful current-release check", () => {
    expect(updateNoticeMessage({ state: "up-to-date", version: "0.1.9" }))
      .toBe("OpenCore 0.1.9 is up to date.");
    expect(updateNoticeTimeout("up-to-date")).toBe(5_000);
  });

  it("does not manufacture a success message for unknown update states", () => {
    expect(updateNoticeMessage({ state: "unexpected" })).toBeNull();
    expect(updateNoticeTimeout("unexpected")).toBeNull();
  });
});
