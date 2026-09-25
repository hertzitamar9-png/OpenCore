import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EchoReceipt } from "./AssistantConversation";
import type { TimelineEntry } from "./types";

const receipt = (id: number, title: string, content: string): TimelineEntry => ({
  id,
  conversationId: "conversation-1",
  timestamp: "2026-09-25T00:00:00Z",
  kind: "echo",
  role: "system",
  source: "ECHO",
  title,
  content,
  metadata: { compactBoundary: id },
});

describe("EchoReceipt", () => {
  it("renders multiple memory events in one card and keeps every event's details", () => {
    const { container } = render(<EchoReceipt entries={[
      receipt(1, "First compact", "First record"),
      receipt(2, "Second compact", "Second record"),
      receipt(3, "Third compact", "Third record"),
    ]} />);

    const cards = container.querySelectorAll(".echo-storage-card");
    expect(cards).toHaveLength(1);
    expect(cards[0].querySelector("summary")?.textContent).toContain("3 updates combined");
    const body = cards[0].querySelector("pre")?.textContent || "";
    for (const text of ["First compact", "First record", "Second compact", "Second record", "Third compact", "Third record", '"compactBoundary": 3']) {
      expect(body).toContain(text);
    }
  });
});
