import { describe, expect, it } from "vitest";
import { requestReasoningEffort } from "./AssistantConversation";

describe("Fast mode effort selection", () => {
  it("uses a bounded fast preset without overwriting the chosen standard effort", () => {
    expect(requestReasoningEffort("high", true)).toBe("fast");
    expect(requestReasoningEffort("high", false)).toBe("high");
  });
});
