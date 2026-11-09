import type { ReactNode } from "react";
import type { ChatQueueItem } from "./types";
export function useQueuedPromptEditor(): {
  id: string | null;
  begin: (id: string) => void;
  hold: (resume: () => void) => boolean;
  finish: () => void;
  stop: () => void;
};
export function QueuedPromptEditor(props: {
  item: ChatQueueItem;
  editor: ReturnType<typeof useQueuedPromptEditor>;
  save: (id: string, text: string) => void;
}): ReactNode;
