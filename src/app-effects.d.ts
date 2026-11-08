import type { ReactNode } from "react";
import type { listen } from "@tauri-apps/api/event";

export function playAppEffect(text: string): boolean;
export function useAppEffects(configuration: { easterEggsEnabled?: boolean; appearance?: { reducedMotion?: boolean } } | null, nativeListen?: typeof listen): { className: string; overlay: ReactNode };
export function AppEffectPreviews(): ReactNode;
