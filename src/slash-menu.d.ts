import type { ReactNode, RefObject } from "react";
import type { ComposerSkillId } from "./composer-skills";

type SlashItem = { id: ComposerSkillId; label: string; description: string };
export function SlashMenu(props: {
  items: SlashItem[];
  input: RefObject<HTMLTextAreaElement | null>;
  onSelect: (id: ComposerSkillId) => void;
  onClose: () => void;
  available?: (item: SlashItem) => boolean;
}): ReactNode;
export function matchesSlashSkillName(item: { id: string; label: string }, query: string): boolean;
export function slashCommandAt(text: string, caret?: number): { start: number; end: number; query: string } | null;
export function takeSlashCommand(text: string, caret: number): { text: string; caret: number };
export function applySlashSelection(editor: HTMLTextAreaElement | null, text: string, update: (text: string) => void): void;
export function ComposerSkillLinks(props: { skills: ComposerSkillId[]; table: SlashItem[]; remove: (id: ComposerSkillId) => void }): ReactNode;
export function isDesktopChromeLaunch(text: string): boolean;
