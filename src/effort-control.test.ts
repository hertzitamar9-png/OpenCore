import { describe, expect, it } from "vitest";
import { effortIndexAt, effortThumbPercent } from "./effort-control";

describe("effort track", () => {
  it("centers the handle on each of seven equal stops", () => {
    expect(effortThumbPercent(0)).toBeCloseTo(100 / 14);
    expect(effortThumbPercent(3)).toBe(50);
    expect(effortThumbPercent(6)).toBeCloseTo(100 - 100 / 14);
    for (let index = 0; index < 7; index += 1) {
      expect(effortIndexAt(effortThumbPercent(index), 0, 100)).toBe(index);
    }
  });

  it("clamps dragging outside the bar to its endpoints", () => {
    expect(effortIndexAt(-20, 0, 100)).toBe(0);
    expect(effortIndexAt(120, 0, 100)).toBe(6);
  });
});
