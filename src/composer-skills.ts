export type ComposerSkillId = "computer-use" | "browser-use" | "chrome-control";

export const COMPOSER_SKILLS: { id: ComposerSkillId; label: string; description: string; aliases: string[] }[] = [
  { id: "computer-use", label: "Computer use", description: "Use Windows apps, terminal, and the on-demand 0.8B screen model", aliases: ["computer", "desktop", "windows", "skills"] },
  { id: "browser-use", label: "OpenCore Browser", description: "Use the isolated in-app browser for this prompt", aliases: ["browser", "web", "skills"] },
  { id: "chrome-control", label: "Chrome control", description: "Control paired Chrome tabs, including developer-console evaluation", aliases: ["chrome", "browser-tabs", "devtools", "skills"] },
];

export function filterComposerSkills(draft: string) {
  if (!draft.startsWith("/")) return [];
  const query = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  if (!query || query === "skills") return COMPOSER_SKILLS;
  return COMPOSER_SKILLS.filter((skill) => [skill.id, ...skill.aliases].some((name) => name.startsWith(query)));
}

export function resolveSlashSkill(draft: string, id: ComposerSkillId) {
  const skill = COMPOSER_SKILLS.find((item) => item.id === id);
  if (!skill || !draft.startsWith("/")) return draft;
  const token = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  if (token === "skills") return draft.slice(token.length + 1).trimStart();
  if (![skill.id, ...skill.aliases].some((name) => name.startsWith(token))) return draft;
  return draft.slice(token.length + 1).trimStart();
}
