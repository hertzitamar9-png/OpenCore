export type ComposerSkillId = "text" | "speech" | "computer-use" | "browser-use" | "chrome-control" | "music" | "image" | "3d" | "3d-animation" | "2d-animation";

export const COMPOSER_SKILLS: { id: ComposerSkillId; label: string; description: string; aliases: string[]; category?: string }[] = [
  { id: "text", label: "Text", description: "Write, code, and plan with the selected text model", aliases: ["code"], category: "text" },
  { id: "speech", label: "Speech", description: "Transcribe audio with an installed speech model", aliases: ["transcribe", "audio"], category: "speech" },
  { id: "music", label: "Music", description: "Compose lyrics and settings, then generate in Music Studio", aliases: ["song"], category: "music" },
  { id: "image", label: "2D images", description: "Generate images and inspect the job in Game Dev Studio", aliases: ["2d", "images"], category: "image" },
  { id: "3d", label: "3D assets", description: "Create a 3D asset in Game Dev Studio", aliases: ["asset", "mesh"], category: "3d" },
  { id: "3d-animation", label: "3D animation", description: "Generate motion or animate an asset in Game Dev Studio", aliases: ["motion", "animate-3d"], category: "3d-animation" },
  { id: "2d-animation", label: "2D animation", description: "Generate a 2D animation in Game Dev Studio", aliases: ["animate-2d"], category: "2d-animation" },
  { id: "computer-use", label: "Computer use", description: "Use Windows apps, terminal, and the on-demand screen model", aliases: ["computer", "desktop", "windows", "skills"], category: "computer-use" },
  { id: "browser-use", label: "OpenCore Browser", description: "Use the isolated in-app browser for this prompt", aliases: ["browser", "web", "skills"] },
  { id: "chrome-control", label: "Chrome control", description: "Control paired Chrome tabs, including developer-console evaluation", aliases: ["chrome", "browser-tabs", "devtools", "skills"] },
];

export function availableComposerSkills(models: {category?: string; installed: boolean}[]) {
  const categories = new Set(models.filter(model => model.installed).map(model => model.category));
  return COMPOSER_SKILLS.filter(skill => !skill.category || categories.has(skill.category));
}
export function exactSlashSkill(draft:string) {
  if(!draft.startsWith('/'))return undefined;
  const token=draft.slice(1).split(/\s/,1)[0].toLowerCase();
  return COMPOSER_SKILLS.find(skill=>[skill.id,...skill.aliases].includes(token));
}
export function filterComposerSkills(draft: string, models?: {category?: string; installed: boolean}[]) {
  if (!draft.startsWith("/")) return [];
  const query = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  const skills = models ? availableComposerSkills(models) : COMPOSER_SKILLS;
  if (!query || query === "skills") return skills;
  return skills.filter((skill) => [skill.id, ...skill.aliases].some((name) => name.startsWith(query)));
}

export function resolveSlashSkill(draft: string, id: ComposerSkillId) {
  const skill = COMPOSER_SKILLS.find((item) => item.id === id);
  if (!skill || !draft.startsWith("/")) return draft;
  const token = draft.slice(1).split(/\s/, 1)[0].toLowerCase();
  if (token === "skills") return draft.slice(token.length + 1).trimStart();
  if (![skill.id, ...skill.aliases].some((name) => name.startsWith(token))) return draft;
  return draft.slice(token.length + 1).trimStart();
}
