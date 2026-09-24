export type ComposerSkillId = "computer-use" | "chrome-control";

export const COMPOSER_SKILLS: { id: ComposerSkillId; label: string; description: string; aliases: string[] }[] = [
  { id: "computer-use", label: "Computer use", description: "Use Windows apps, desktop, terminal, and OpenCore Browser", aliases: ["computer", "desktop", "windows"] },
  { id: "chrome-control", label: "Chrome control", description: "Use paired Chrome tabs and your Chrome profile", aliases: ["chrome", "browser-tabs"] },
];

export function filterComposerSkills(draft: string) {
  if (!draft.startsWith("/")) return [];
  const query = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  if (!query) return COMPOSER_SKILLS;
  return COMPOSER_SKILLS.filter((skill) => [skill.id, ...skill.aliases].some((name) => name.startsWith(query)));
}

export function resolveSlashSkill(draft: string, id: ComposerSkillId) {
  const skill = COMPOSER_SKILLS.find((item) => item.id === id);
  if (!skill || !draft.startsWith("/")) return draft;
  const token = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  if (![skill.id, ...skill.aliases].some((name) => name.startsWith(token))) return draft;
  return draft.slice(token.length + 1).trimStart();
}
