import { Check } from "lucide-react";
import type { RuntimeProfile } from "./types";

export const selectableModelProfiles: { id: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">; label: string; description: string }[] = [
  { id: "echo", label: "ECHO 3T", description: "Addressable history target · exact archive" },
  { id: "native1m", label: "1M extended", description: "1,000,000-token YaRN window · trained context 262,144" },
  { id: "doucode", label: "DuoCore", description: "K2 + Nanbeige · competing drafts, one selected answer" },
  { id: "dualcore-kv", label: "DualCore KV", description: "Two LFM Q8 brains · native 131K context" },
  { id: "dualcore-echo", label: "DualCore ECHO", description: "Two LFM Q8 brains · recomputed prefixes, ECHO archive" },
  { id: "fusioncore-kv", label: "FusionCore KV", description: "Two coupled LFM towers · one token loop, 131K" },
  { id: "fusioncore-echo", label: "FusionCore ECHO", description: "Coupled LFM towers · prefix recomputation, 8K active" },
];

export const profileLabel = (profile: string) => selectableModelProfiles.find((item) => item.id === profile)?.label ?? (profile === "unsloth-echo" ? "Unsloth + ECHO" : "Stopped");

export const profileDescription = (profile: RuntimeProfile) =>
  selectableModelProfiles.find((item) => item.id === profile)?.description ?? "Unsloth backend · ECHO archive";

export function ModelProfileOptions({ selectedProfile, onSelect, disabled = false, id = "model-profile-options", className = "" }: {
  selectedProfile: RuntimeProfile;
  onSelect: (profile: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">) => void;
  disabled?: boolean;
  id?: string;
  className?: string;
}) {
  return <div className={`model-picker-options ${className}`} id={id} role="group" aria-label="Choose model profile">
    {selectableModelProfiles.map(({ id: profile, label, description }) => <button
      key={profile}
      className={`model-picker-option ${selectedProfile === profile ? "selected" : ""}`}
      type="button"
      aria-pressed={selectedProfile === profile}
      disabled={disabled}
      onClick={() => onSelect(profile)}
    >
      <span><strong>{label}</strong><small>{description}</small></span>
      {selectedProfile === profile ? <Check size={16} aria-hidden="true" /> : null}
    </button>)}
  </div>;
}
