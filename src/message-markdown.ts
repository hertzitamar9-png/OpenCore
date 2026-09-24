const localFileMarkdownLink =
  /\[([^\]]+)]\(\s*<(?:(?:file:\/\/\/)?\/?[A-Za-z]:[\\/])[^>]*>\s*\)/g;

export function sanitizeMessageMarkdown(value: string): string {
  const clarification = value.trim().match(/^<send_user_message_question_reply>\s*([\s\S]*?)\s*<\/send_user_message_question_reply>$/i);
  if (clarification) {
    try {
      const rows = JSON.parse(clarification[1]) as Array<{ answer?: unknown }>;
      const answers = rows.map((row) => row.answer).filter((answer): answer is string => typeof answer === "string" && answer.trim().length > 0);
      return answers.join("\n") || "Answered a clarification";
    } catch { return "Answered a clarification"; }
  }
  return value.replace(localFileMarkdownLink, "$1");
}
