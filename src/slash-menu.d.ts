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
