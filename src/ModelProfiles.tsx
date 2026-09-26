import { Check } from "lucide-react";
import type { RuntimeProfile } from "./types";

export const selectableModelProfiles: { id: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">; label: string; description: string }[] = [
  { id: "echo", label: "ECHO 3T", description: "Addressable history target · exact archive" },
  { id: "native1m", label: "1M extended", description: "1,000,000-token YaRN window · trained context 262,144" },
  { id: "doucode", label: "doUcode", description: "K2 + Nanbeige · persistent ECHO archive" },
];

export const profileLabel = (profile: string) => profile === "echo" ? "ECHO 3T" : profile === "native1m" ? "1M extended" : profile === "unsloth-echo" ? "Unsloth + ECHO" : profile === "doucode" ? "doUcode" : "Stopped";

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
