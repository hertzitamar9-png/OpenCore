import { describe, expect, it } from "vitest";
import { formatMessageTimestamp } from "./message-time";

describe("message timestamp", () => {
  it("shows exact seconds together with day, month, and full year", () => {
    const timestamp = new Date(2026, 8, 2, 16, 12, 0).toISOString();

    expect(formatMessageTimestamp(timestamp)).toBe("16:12:00 · 02/09/2026");
  });

  it("renders no timestamp for a missing value", () => {
    expect(formatMessageTimestamp(undefined)).toBe("");
  });
});
