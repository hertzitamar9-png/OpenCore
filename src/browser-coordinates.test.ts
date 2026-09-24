import { describe, expect, it } from "vitest";
import { browserPoint } from "./browser-coordinates";

describe("browser screenshot coordinates", () => {
  it("scales clicks to the Chrome viewport and clamps edges", () => {
    expect(browserPoint(500, 250, 100, 50, 800, 400, 1600, 800)).toEqual({ x: 800, y: 400 });
    expect(browserPoint(0, 0, 100, 50, 800, 400, 1600, 800)).toEqual({ x: 0, y: 0 });
    expect(browserPoint(900, 450, 100, 50, 800, 400, 1600, 800)).toEqual({ x: 1599, y: 799 });
  });
});
