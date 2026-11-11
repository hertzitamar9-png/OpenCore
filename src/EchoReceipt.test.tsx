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
  it('reports an archive failure honestly within the same single receipt', () => {
    const { container, queryByText } = render(<EchoReceipt entries={[{...receipt(1, 'ECHO', 'Archive write failed'), metadata:{artifact:{status:'failed',words:0}}}]} />);
    expect(container.querySelectorAll('.echo-storage-card')).toHaveLength(1);
    expect(container.querySelector('summary')).toHaveTextContent('ECHO save needs attention');
    expect(queryByText('Saved to ECHO')).toBeNull();
  });
  it('keeps verification, memory and archive details inside one Saved to ECHO receipt', () => {
    const verification = { ...receipt(1, 'Checks and changes', 'Command evidence: 1 exit code 0.'), kind: 'progress', metadata: { verificationLevel: 'max' } };
    const memory = { ...receipt(2, 'Automatic memory', 'Fact extraction skipped; no new memories saved'), kind: 'progress', metadata: { status: 'skipped' } };
    const archive = { ...receipt(3, 'ECHO archive', '11 words archived'), metadata: { artifact: { status: 'complete', words: 11 } } };
    const { container, getByText, queryByText } = render(<EchoReceipt entries={[verification, memory, archive]} />);
    expect(container.querySelectorAll('.echo-storage-card')).toHaveLength(1);
    expect(container.querySelector('summary')).toHaveTextContent('Saved to ECHO');
    expect(container.querySelector('summary')).toHaveTextContent('11 words archived');
    expect(queryByText('Memory receipt')).toBeNull();
    expect(queryByText('Verification receipt')).toBeNull();
    expect(getByText('Command evidence: 1 exit code 0.')).toBeInTheDocument();
    expect(getByText('Fact extraction skipped; no new memories saved')).toBeInTheDocument();
  });
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
