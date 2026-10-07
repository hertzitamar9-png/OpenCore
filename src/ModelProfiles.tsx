import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { modelLibrary } from "./api";
import type { RuntimeProfile } from "./types";
import modelCatalog from "../src-tauri/resources/model-catalog.json";

const catalogProfiles = new Map((modelCatalog.models as { id: string; label: string; description: string; selectable: boolean }[])
  .filter(model => model.selectable).map(model => [model.id, model]));

type InstalledModelProfile = { id: string; label: string; description: string };

export function useInstalledModelProfiles() {
  const [installedProfiles, setInstalledProfiles] = useState<InstalledModelProfile[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void modelLibrary({ fresh: true }).then(library => {
      if (active) setInstalledProfiles(library.models.filter(model => model.installed && model.selectable)
        .map(model => ({ id: model.id, label: catalogProfiles.get(model.id)?.label || model.label, description: model.description })));
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  return { installedProfiles, error };
}

export function profilesForInstalledModels(installedProfiles: InstalledModelProfile[]) {
  const installedIds = new Set(installedProfiles.map(profile => profile.id));
  return [
    ...selectableModelProfiles.filter(profile => installedIds.has(profile.id)),
    ...installedProfiles.filter(profile => !selectableModelProfiles.some(known => known.id === profile.id)),
  ];
}

export const selectableModelProfiles: { id: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">; label: string; description: string }[] = [
  { id: "echo", label: "ECHO 3T", description: "Addressable history target · exact archive" },
  { id: "native1m", label: "1M extended · ECHO", description: "1,000,000-token YaRN window · ECHO archive · trained context 262,144" },
  { id: "doucode", label: "DuoCore · ECHO", description: "K2 + Nanbeige · competing drafts, one selected answer · ECHO archive" },
  { id: "nanbeige-bf16", label: "Nanbeige BF16", description: "One Nanbeige model · BF16 weights" },
  { id: "nanbeige-bf16-echo", label: "Nanbeige BF16 ECHO", description: "One Nanbeige model · ECHO archive retrieval" },
  { id: "dualcore-kv", label: "DualCore KV", description: "Two LFM Q8 brains · 131K native context" },
  { id: "dualcore-echo", label: "DualCore ECHO", description: "Two LFM Q8 brains · ECHO archive · incremental KV" },
  { id: "fusioncore-kv", label: "FusionCore KV", description: "Two coupled LFM towers · 131K native context" },
  { id: "fusioncore-echo", label: "FusionCore ECHO", description: "Coupled LFM towers · ECHO archive · incremental KV" },
  { id: "swift-27b", label: "Swift 1.5 · ECHO", description: "Optional 27B IQ2_S · ECHO recall · 16K attention" },
  { id: "dirk-27b", label: "Dirk Vision · ECHO", description: "Optional 27B IQ2_S + vision · ECHO recall · 8K attention" },
  { id: "davidau-27b", label: "DavidAU Turbo · ECHO", description: "Optional 27B IQ2_M · CPU offload · ECHO recall" },
  { id: "oxcoder-9b", label: "OxCoder 9B · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "nim-2-coder-7b", label: "NIM-2 Coder 7B · ECHO", description: "Optional Q4_K_M coding model · ECHO archive · 16K attention" },
  { id: "ternary-bonsai-2-27b", label: "Ternary Bonsai 2 27B · ECHO", description: "Optional PTQ1_0 coding model · ECHO archive · 16K attention" },
  { id: "mimo-distill-qwen-9b", label: "MiMo Distill Qwen 9B · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "frognano-4b", label: "FrogNano 4B · ECHO", description: "Optional Q8_0 multimodal coding model · ECHO archive · 16K attention" },
  { id: "qwen38-distill-9b", label: "Qwen 3.8 Distill 9B · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "triumvirate-9b-coder", label: "Triumvirate 9B Coder · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "orion-agentic-9b", label: "Orion Agentic 9B · ECHO", description: "Optional Q6_K coding model · ECHO archive · 16K attention" },
  { id: "zenith-9b-codecore", label: "Zenith CodeCore 9B · ECHO", description: "Optional Q5_K_M coding model · ECHO archive · 16K attention" },
  { id: "neohorse-1-9b", label: "NeoHorse 1 9B · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "boomslang-3b", label: "Boomslang 3B · ECHO", description: "Optional GGUF coding model · ECHO archive · 16K attention" },
  { id: "tiel-inspired-coder-9b", label: "Tiel-Inspired Coder 9B · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "gmcoder", label: "Gmcoder · ECHO", description: "Optional Q8_0 coding model · ECHO archive · 16K attention" },
  { id: "ornith-1-5-9b-mtp", label: "Ornith 1.5 9B MTP · ECHO", description: "Optional Q4_K_M with MTP draft head · ECHO archive · 16K attention" },
];

export const profileLabel = (profile: string) => selectableModelProfiles.find((item) => item.id === profile)?.label ?? catalogProfiles.get(profile)?.label ?? (profile === "unsloth-echo" ? "Unsloth + ECHO" : "Stopped");

export const profileDescription = (profile: RuntimeProfile) =>
  selectableModelProfiles.find((item) => item.id === profile)?.description ?? catalogProfiles.get(profile)?.description ?? "Unsloth backend · ECHO archive";

export const isSelectableModelProfile = (profile: string) =>
  profile === "unsloth-echo" || selectableModelProfiles.some(item => item.id === profile) || catalogProfiles.has(profile);

export function ModelProfileOptions({ selectedProfile, onSelect, disabled = false, id = "model-profile-options", className = "" }: {
  selectedProfile: RuntimeProfile;
  onSelect: (profile: Exclude<RuntimeProfile, "stopped" | "unsloth-echo">) => void;
  disabled?: boolean;
  id?: string;
  className?: string;
}) {
  const { installedProfiles, error } = useInstalledModelProfiles();
  const profiles = profilesForInstalledModels(installedProfiles || []);
  return <div className={`model-picker-options ${className}`} id={id} role="group" aria-label="Choose model profile">
    {error ? <p className="model-picker-empty" role="alert">Could not check downloaded models. Close and reopen to retry.</p>
      : installedProfiles === null ? <p className="model-picker-empty" role="status">Checking downloaded models…</p>
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
