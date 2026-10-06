import type { SpeechStatus } from "./api";

const loadingPhases: Record<string, string> = {
  warming: "Loading speech weights into system RAM for standby…",
  "starting-runtime": "Starting speech runtime…",
  "verifying-checkpoint": "Checking installed speech files…",
  "building-model": "Preparing speech model…",
  "expanding-weights": "Expanding speech weights…",
  "applying-weights": "Loading speech weights…",
  "preparing-processor": "Preparing speech recognition…",
  "activating-device": "Preparing microphone…",
};

export function speechLoadingMessage(status: SpeechStatus | null): string | null {
  const message = status && loadingPhases[status.phase];
  if (!message) return null;
  return `${message}${status?.loadingElapsedMs == null ? "" : ` · ${(status.loadingElapsedMs / 1000).toFixed(1)} s`}`;
}
