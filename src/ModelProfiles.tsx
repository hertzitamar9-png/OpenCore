import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { modelLibrary } from "./api";
import type { RuntimeProfile } from "./types";

export const selectableModelProfiles: { id: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">; label: string; description: string }[] = [
  { id: "echo", label: "ECHO 3T", description: "Addressable history target · exact archive" },
  { id: "native1m", label: "1M extended", description: "1,000,000-token YaRN window · trained context 262,144" },
  { id: "doucode", label: "DuoCore", description: "K2 + Nanbeige · competing drafts, one selected answer" },
  { id: "nanbeige-bf16", label: "Nanbeige BF16", description: "One Nanbeige model · BF16 weights" },
  { id: "nanbeige-bf16-echo", label: "Nanbeige BF16 ECHO", description: "One Nanbeige model · ECHO archive retrieval" },
  { id: "dualcore-kv", label: "DualCore KV", description: "Two LFM Q8 brains · 131K native context" },
  { id: "dualcore-echo", label: "DualCore ECHO", description: "Two LFM Q8 brains · ECHO archive · incremental KV" },
  { id: "fusioncore-kv", label: "FusionCore KV", description: "Two coupled LFM towers · 131K native context" },
  { id: "fusioncore-echo", label: "FusionCore ECHO", description: "Coupled LFM towers · ECHO archive · incremental KV" },
  { id: "swift-27b", label: "Swift 1.5", description: "Optional 27B IQ2_S · ECHO recall · 16K attention" },
  { id: "dirk-27b", label: "Dirk Vision", description: "Optional 27B IQ2_S + vision · ECHO recall · 8K attention" },
  { id: "davidau-27b", label: "DavidAU Turbo", description: "Optional 27B IQ2_M · CPU offload · ECHO recall" },
  { id: "oxcoder-9b", label: "OxCoder 9B", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "nim-2-coder-7b", label: "NIM-2 Coder 7B", description: "Optional Q4_K_M coding model · ECHO archive · 16K attention" },
  { id: "ternary-bonsai-2-27b", label: "Ternary Bonsai 2 27B", description: "Optional PTQ1_0 coding model · ECHO archive · 16K attention" },
  { id: "mimo-distill-qwen-9b", label: "MiMo Distill Qwen 9B", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "frognano-4b", label: "FrogNano 4B", description: "Optional Q8_0 multimodal coding model · ECHO archive · 16K attention" },
  { id: "qwen38-distill-9b", label: "Qwen 3.8 Distill 9B", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "triumvirate-9b-coder", label: "Triumvirate 9B Coder", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "orion-agentic-9b", label: "Orion Agentic 9B", description: "Optional Q6_K coding model · ECHO archive · 16K attention" },
  { id: "zenith-9b-codecore", label: "Zenith CodeCore 9B", description: "Optional Q5_K_M coding model · ECHO archive · 16K attention" },
  { id: "neohorse-1-9b", label: "NeoHorse 1 9B", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "boomslang-3b", label: "Boomslang 3B", description: "Optional GGUF coding model · ECHO archive · 16K attention" },
  { id: "tiel-inspired-coder-9b", label: "Tiel-Inspired Coder 9B", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "gmcoder", label: "Gmcoder", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "ornith-1-5-9b-mtp", label: "Ornith 1.5 9B MTP", description: "Optional Q4_K_M with MTP draft head · ECHO archive · 16K attention" },
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
  const [installedIds, setInstalledIds] = useState<Set<string> | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void modelLibrary().then(library => {
      if (active) setInstalledIds(new Set(library.models.filter(model => model.installed && model.selectable).map(model => model.id)));
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  const profiles = selectableModelProfiles.filter(profile => installedIds?.has(profile.id));
  return <div className={`model-picker-options ${className}`} id={id} role="group" aria-label="Choose model profile">
    {error ? <p className="model-picker-empty" role="alert">Could not check downloaded models. Close and reopen to retry.</p>
      : installedIds === null ? <p className="model-picker-empty" role="status">Checking downloaded models…</p>
      : profiles.length === 0 ? <p className="model-picker-empty" role="status">No downloaded text models. Install one in Models.</p> : null}
    {profiles.map(({ id: profile, label, description }) => <button
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
