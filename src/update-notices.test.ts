import { describe, expect, it } from "vitest";
import { updateNoticeMessage, updateNoticePercent, updateNoticeTimeout } from "./update-notices";

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

  it("reports real download progress and keeps unknown lengths indeterminate", () => {
    expect(updateNoticePercent({ state: "downloading", downloaded: 25, total: 100 })).toBe(25);
    expect(updateNoticePercent({ state: "downloading", downloaded: 120, total: 100 })).toBe(100);
    expect(updateNoticePercent({ state: "downloading", downloaded: 20 })).toBeNull();
    expect(updateNoticePercent({ state: "downloading", downloaded: 20, total: 0 })).toBeNull();
  });

  it("keeps update progress visible until the installer is launched", () => {
    expect(updateNoticeMessage({ state: "downloading", version: "0.1.10" }))
      .toBe("Downloading OpenCore update 0.1.10…");
    expect(updateNoticeMessage({ state: "installing" }))
      .toBe("Applying update… OpenCore will reopen automatically.");
    expect(updateNoticeTimeout("downloading")).toBeNull();
    expect(updateNoticeTimeout("installing")).toBeNull();
  });
});
