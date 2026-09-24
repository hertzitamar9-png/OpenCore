import { describe, expect, it } from "vitest";
import { clampFloatingRect, moveFloatingRect, resizeFloatingRect } from "./floating-geometry";

describe("floating surfaces", () => {
  it("keeps a moved panel reachable on screen", () => {
    const rect = clampFloatingRect({ x: 900, y: 700, width: 600, height: 400 }, 1200, 800);
    expect(rect).toEqual({ x: 592, y: 392, width: 600, height: 400 });
    expect(moveFloatingRect(rect, -1000, -1000, 1200, 800)).toMatchObject({ x: 8, y: 8 });
  });
  it("resizes from either edge without pushing the panel off screen", () => {
    const start = { x: 200, y: 100, width: 500, height: 400 };
    expect(resizeFloatingRect(start, "se", 200, 100, 900, 700)).toMatchObject({ x: 200, y: 100, width: 692, height: 500 });
    expect(resizeFloatingRect(start, "nw", 100, 80, 900, 700)).toMatchObject({ x: 300, y: 180, width: 400, height: 320 });
  });
});
