import { describe, expect, it } from "vitest";
import { formatMessageTimestamp } from "./message-time";

describe("message timestamp", () => {
  it("shows time together with day, month, and two-digit year", () => {
    const timestamp = new Date(2026, 8, 2, 16, 12, 0).toISOString();

    expect(formatMessageTimestamp(timestamp)).toBe("16:12 · 02/09/26");
  });

  it("renders no timestamp for a missing value", () => {
    expect(formatMessageTimestamp(undefined)).toBe("");
  });
});
