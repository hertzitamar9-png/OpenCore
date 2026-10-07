import type { SpeechStatus } from "./api";

const loadingPhases: Record<string, string> = {
  warming: "Loading speech weights into system RAM for standby…",
  "starting-runtime": "Starting speech runtime…",
  "verifying-checkpoint": "Checking installed speech files…",
  "building-model": "Preparing speech model…",
  "preparing-original-runtime": "Preparing original Phonon runtime…",
  "loading-packed-weights": "Loading original speech weights…",
  "verifying-dense-cache": "Checking prepared speech tensors…",
  "loading-dense-cache": "Loading prepared speech tensors…",
  "expanding-weights": "Expanding speech weights…",
  "applying-weights": "Loading speech weights…",
  "saving-dense-cache": "Preparing faster future speech startup…",
  "preparing-processor": "Preparing speech recognition…",
  "activating-device": "Preparing microphone…",
};

export function speechLoadingMessage(status: SpeechStatus | null): string | null {
  const message = status && loadingPhases[status.phase];
  if (!message) return null;
  return `${message}${status?.loadingElapsedMs == null ? "" : ` · ${(status.loadingElapsedMs / 1000).toFixed(1)} s`}`;
}
