import { describe, expect, it } from "vitest";
import { sanitizeMessageMarkdown } from "./message-markdown";

describe("message Markdown", () => {
  it("preserves an imported local-file target for the file viewer", () => {
    const message =
      'The [brainstorming skill](</C:/Users/hertz/.codex/plugins/cache/superpowers/6.3.0/skills/brainstorming/SKILL.md>) requires approval.';

    expect(sanitizeMessageMarkdown(message)).toBe(message);
  });

  it("preserves real web hyperlinks", () => {
    const message = "Read [the model card](https://huggingface.co/example/model).";

    expect(sanitizeMessageMarkdown(message)).toBe(message);
  });

  it("shows the answer from an imported clarification reply instead of its control envelope", () => {
    const raw = '<send_user_message_question_reply> [{"questionItemId":"id-1","question":"Which file?","answer":"C:\\\\Users\\\\hertz\\\\Documents\\\\a very long path\\\\image.png"}] </send_user_message_question_reply>';
    expect(sanitizeMessageMarkdown(raw)).toBe("C:\\Users\\hertz\\Documents\\a very long path\\image.png");
  });
});
