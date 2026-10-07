import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { serializeSettingsSave, waitForSettingsSaves } from "./useSettingsAutosave";

export type VerificationMode = "no" | "default" | "long" | "max";
export interface AppearanceConfig {
  theme: "dark" | "light" | "system";
  accentColor: string;
  fontFamily: string;
  fontSize: number;
  density: "comfortable" | "compact";
  reducedMotion: boolean;
  highContrast: boolean;
}
export interface McpServer {
  id: string;
  name: string;
  enabled: boolean;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  bearerTokenEnvVar: string | null;
  startupTimeoutSec: number;
  toolTimeoutSec: number;
}
export interface PlatformConfig {
  systemPrompt: string;
  compactAtTokens: number;
  verification: VerificationMode;
  repairAttempts: number;
  skillDirectories: string[];
  pluginDirectories: string[];
  disabledSkills: string[];
  disabledPlugins: string[];
  mcpServers: McpServer[];
  activityEnabled: boolean;
  memoryEnabled: boolean;
  appearance: AppearanceConfig;
}
export type PlatformConfigurationPatch = Omit<Partial<PlatformConfig>, "appearance"> & { appearance?: Partial<AppearanceConfig> };
export type PlatformDraftField = Exclude<keyof PlatformConfig, "appearance"> | `appearance.${keyof AppearanceConfig}`;

export function platformDraftField(configuration: PlatformConfig, field: PlatformDraftField): unknown {
  return field.startsWith("appearance.") ? configuration.appearance[field.slice(11) as keyof AppearanceConfig]
    : configuration[field as Exclude<keyof PlatformConfig, "appearance">];
}

const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
export function platformConfigurationChanges(before: PlatformConfig, after: PlatformConfig): PlatformConfigurationPatch {
  const patch: PlatformConfigurationPatch = {};
  for (const key of Object.keys(after) as (keyof PlatformConfig)[]) {
    if (key === "appearance") {
      const appearance: Partial<AppearanceConfig> = {};
      for (const field of Object.keys(after.appearance) as (keyof AppearanceConfig)[])
        if (!same(before.appearance[field], after.appearance[field])) Object.assign(appearance, { [field]: after.appearance[field] });
      if (Object.keys(appearance).length) patch.appearance = appearance;
    } else if (!same(before[key], after[key])) Object.assign(patch, { [key]: after[key] });
  }
  return patch;
}

// Source updates replace untouched fields while edits, including invalid drafts,
// survive older acknowledgments and changes made by the agent's settings tools.
export function reconcilePlatformDraft(draft: PlatformConfig, before: PlatformConfig, incoming: PlatformConfig, edited?: ReadonlySet<PlatformDraftField>): PlatformConfig {
  const changes = platformConfigurationChanges(before, draft);
  // An A -> B -> A edit can equal an older acknowledged source while its latest
  // write is still queued. Explicit edit ownership outlives that coincidence.
  for (const field of edited ?? []) {
    if (field.startsWith("appearance.")) Object.assign(changes.appearance ??= {}, { [field.slice(11)]: platformDraftField(draft, field) });
    else Object.assign(changes, { [field]: platformDraftField(draft, field) });
  }
  return { ...incoming, ...changes, appearance: { ...incoming.appearance, ...changes.appearance } };
}

export function validPlatformDraft(source: PlatformConfig, draft: PlatformConfig, servers: McpServer[] | null): PlatformConfig {
  let valid = source;
  for (const key of Object.keys(draft) as (keyof PlatformConfig)[]) {
    if (key === "appearance") {
      for (const field of Object.keys(draft.appearance) as (keyof AppearanceConfig)[]) {
        const next = { ...valid, appearance: { ...valid.appearance, [field]: draft.appearance[field] } };
        if (!validatePlatformConfiguration(next).length) valid = next;
      }
    } else {
      if (key === "mcpServers" && servers === null) continue;
      const next = { ...valid, [key]: key === "mcpServers" ? servers : draft[key] } as PlatformConfig;
      if (!validatePlatformConfiguration(next).length) valid = next;
    }
  }
  return valid;
}
export interface PlatformSkill {
  id: string; name: string; description: string; source: string;
  path: string | null; enabled: boolean; pluginId: string | null;
}
export interface PlatformPlugin {
  id: string; name: string; description: string; version: string | null; path: string;
  enabled: boolean; skills: string[]; mcpServers: string[]; warnings: string[];
}
export interface PlatformActivity {
  id: string; timestamp: string; category: string; action: string; summary: string;
  source: string; details: unknown; conversationId: string | null; projectId: string | null;
}
export interface PlatformMemory {
  id: string; key: string; kind: "fact" | "lesson"; content: string; source: string;
  createdAt: string; updatedAt: string; version: number; supersedes: string | null;
  scope: string; status: "active" | "superseded"; evidence: string | null;
}
export interface TestingLabProfile {
  id: string; label: string; kind: "virtualbox" | "android"; enabled: boolean; executable: string;
  vmName?: string | null; deviceSerial?: string | null; avdName?: string | null; emulatorExecutable?: string | null;
  sdkRoot?: string | null; avdHome?: string | null; androidUserHome?: string | null; emulatorPort?: number | null;
  guestUser?: string | null; passwordEnv?: string | null;
}
export interface TestingLabAction {
  action: "list" | "status" | "start" | "stop" | "inspect" | "screenshot" | "install_app" | "launch_app" | "tap" | "key" | "text";
  profileId?: string; path?: string; args?: string[]; x?: number; y?: number; text?: string; key?: string;
}

export const PLATFORM_SETTINGS_EVENT = "opencore-agent-settings-changed";
export const DEFAULT_ACCENT_COLOR = "#245ca8";
export const VERIFICATION_OPTIONS: { value: VerificationMode; label: string; description: string }[] = [
  { value: "no", label: "No checks", description: "Skip added review turns. The requested work and its acceptance criteria still apply." },
  { value: "default", label: "Default", description: "A bounded review of changed work with evidence from the tools used." },
  { value: "long", label: "Long", description: "More review time for failure cases, regressions, and the original request." },
  { value: "max", label: "Max", description: "The largest bounded review budget. Extra review does not guarantee correctness." },
];

export function defaultPlatformConfiguration(): PlatformConfig {
  return {
    systemPrompt: "", compactAtTokens: 200000, verification: "default", repairAttempts: 3,
    skillDirectories: [], pluginDirectories: [], disabledSkills: [], disabledPlugins: [],
    mcpServers: [], activityEnabled: true, memoryEnabled: true,
    appearance: { theme: "dark", accentColor: DEFAULT_ACCENT_COLOR, fontFamily: "system", fontSize: 14,
      density: "comfortable", reducedMotion: false, highContrast: false },
  };
}

const boundedInteger = (value: unknown, min: number, max: number): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
const environmentName = /^[A-Za-z_][A-Za-z0-9_]*$/;
const connectionIdentifier = /^[A-Za-z0-9_-]{1,80}$/;
const absoluteDirectory = /^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+|\/)/;
const utf8Bytes = (text: string) => new TextEncoder().encode(text).length;

export function directoryLines(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).map(value => value.trim()).filter(Boolean))];
}

function mcpErrors(servers: McpServer[]): string[] {
  const errors: string[] = [];
  const ids = new Set<string>(), names = new Set<string>();
  if (servers.length > 64) errors.push("MCP supports at most 64 profiles.");
  servers.forEach((server, index) => {
    const prefix = `MCP profile ${index + 1}`;
    if (!connectionIdentifier.test(server.id) || !connectionIdentifier.test(server.name))
      errors.push(`${prefix}: id and name must use 1–80 letters, numbers, underscores, or hyphens.`);
    const id = server.id.toLowerCase(), name = server.name.toLowerCase();
    if (id === "opencore" || name === "opencore") errors.push(`${prefix}: OpenCore is a reserved id and name.`);
    if (ids.has(id) || names.has(name)) errors.push(`${prefix}: ids and names must be unique (case insensitive).`);
    ids.add(id); names.add(name);
    const command = server.command !== null, url = server.url !== null;
    if (command === url) errors.push(`${prefix}: specify exactly one command or HTTP URL.`);
    if (command && (!server.command?.trim() || utf8Bytes(server.command) > 4096 || server.command.includes("\0"))) errors.push(`${prefix}: command must contain text and be at most 4,096 UTF-8 bytes.`);
    if (server.args.length > 128 || Object.keys(server.env).length > 128) errors.push(`${prefix}: args and env each support at most 128 entries.`);
    if (server.args.some(arg => utf8Bytes(arg) > 8192 || arg.includes("\0"))) errors.push(`${prefix}: arguments must be at most 8,192 UTF-8 bytes and contain no null characters.`);
    if (Object.values(server.env).some(value => utf8Bytes(value) > 16384 || value.includes("\0"))) errors.push(`${prefix}: environment values must be at most 16,384 UTF-8 bytes and contain no null characters.`);
    if (command && server.bearerTokenEnvVar !== null) errors.push(`${prefix}: bearerTokenEnvVar applies only to HTTP profiles.`);
    if (url) {
      if (server.args.length || Object.keys(server.env).length) errors.push(`${prefix}: HTTP profiles cannot use stdio args or env; reference an existing bearerTokenEnvVar instead.`);
      try {
        const parsed = new URL(server.url!);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") errors.push(`${prefix}: URL must use HTTP or HTTPS.`);
        if (parsed.username || parsed.password) errors.push(`${prefix}: URL credentials are not allowed. Use an environment variable.`);
        if (parsed.hash) errors.push(`${prefix}: URL fragments are not allowed.`);
        if (!parsed.hostname || utf8Bytes(server.url!) > 8192) errors.push(`${prefix}: URL requires a host and at most 8,192 UTF-8 bytes.`);
      } catch { errors.push(`${prefix}: enter a valid HTTP or HTTPS URL.`); }
    }
    if (Object.keys(server.env).some(key => !environmentName.test(key))) errors.push(`${prefix}: environment names must be valid variable names.`);
    if (server.bearerTokenEnvVar !== null && !environmentName.test(server.bearerTokenEnvVar)) errors.push(`${prefix}: bearer token environment name is invalid.`);
    if (!boundedInteger(server.startupTimeoutSec, 1, 600)) errors.push(`${prefix}: startup timeout must be 1–600 seconds.`);
    if (!boundedInteger(server.toolTimeoutSec, 1, 3600)) errors.push(`${prefix}: tool timeout must be 1–3,600 seconds.`);
  });
  return errors;
}

export function validatePlatformConfiguration(configuration: PlatformConfig): string[] {
  const errors: string[] = [];
  if (utf8Bytes(configuration.systemPrompt) > 32768 || configuration.systemPrompt.includes("\0")) errors.push("Additional instructions must be at most 32,768 UTF-8 bytes and contain no null characters.");
  if (!boundedInteger(configuration.compactAtTokens, 1024, 3000000)) errors.push("Compaction must be an integer from 1,024 to 3,000,000 tokens.");
  if (!VERIFICATION_OPTIONS.some(option => option.value === configuration.verification)) errors.push("Select a valid verification level.");
  if (!boundedInteger(configuration.repairAttempts, 0, 10)) errors.push("Repair attempts must be an integer from 0 to 10.");
  for (const [name, values] of [["Skill", configuration.skillDirectories], ["Plugin", configuration.pluginDirectories]] as const) {
    if (values.length > 32) errors.push(`${name} directories are limited to 32 paths.`);
    if (values.some(value => !absoluteDirectory.test(value) || /[\u0000-\u001f]/.test(value) || utf8Bytes(value) > 4096)) errors.push(`${name} directories must be absolute paths with at most 4,096 UTF-8 bytes, one per line.`);
  }
  if (configuration.disabledSkills.length > 512 || configuration.disabledPlugins.length > 512) errors.push("At most 512 skills or plugins may be disabled.");
  const appearance = configuration.appearance;
  if (!["dark", "light", "system"].includes(appearance.theme)) errors.push("Select a valid theme.");
  if (!/^#[0-9a-fA-F]{6}$/.test(appearance.accentColor)) errors.push("Accent color must use #RRGGBB.");
  if (!appearance.fontFamily.trim() || utf8Bytes(appearance.fontFamily) > 128 || /[\u0000-\u001f;{}<>]/.test(appearance.fontFamily)) errors.push("Font family must be a plain name with at most 128 UTF-8 bytes.");
  if (!boundedInteger(appearance.fontSize, 10, 24)) errors.push("Text size must be an integer from 10 to 24 pixels.");
  if (!["comfortable", "compact"].includes(appearance.density)) errors.push("Select a valid spacing density.");
  return [...errors, ...mcpErrors(configuration.mcpServers)];
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
  return value as Record<string, unknown>;
}
function optionalString(record: Record<string, unknown>, key: string, label: string): string | null {
  const value = record[key];
  if (value == null) return null;
  if (typeof value !== "string") throw new Error(`${label}: ${key} must be text or null.`);
  return value;
}

export function parseMcpServers(text: string): McpServer[] {
  let data: unknown;
  try { data = JSON.parse(text); } catch (error) { throw new Error(`Invalid MCP JSON: ${errorMessage(error)}`); }
  if (!Array.isArray(data)) throw new Error("MCP profiles must be a JSON array.");
  const servers: McpServer[] = data.map((value, index) => {
    const label = `MCP profile ${index + 1}`, record = object(value, label);
    const known = new Set(["id", "name", "enabled", "command", "args", "env", "url", "bearerTokenEnvVar", "startupTimeoutSec", "toolTimeoutSec"]);
    const unknown = Object.keys(record).filter(key => !known.has(key));
    if (unknown.length) throw new Error(`${label}: unknown field ${unknown.join(", ")}.`);
    if (typeof record.id !== "string" || typeof record.name !== "string") throw new Error(`${label}: id and name are required.`);
    if (record.enabled !== undefined && typeof record.enabled !== "boolean") throw new Error(`${label}: enabled must be true or false.`);
    const args = record.args ?? [];
    if (!Array.isArray(args) || args.some(arg => typeof arg !== "string")) throw new Error(`${label}: args must be an array of strings.`);
    const env = object(record.env ?? {}, `${label} environment`);
    if (Object.values(env).some(item => typeof item !== "string")) throw new Error(`${label}: environment values must be strings.`);
    return {
      id: record.id, name: record.name, enabled: record.enabled !== false,
      command: optionalString(record, "command", label), url: optionalString(record, "url", label),
      args: args as string[], env: env as Record<string, string>,
      bearerTokenEnvVar: optionalString(record, "bearerTokenEnvVar", label),
      startupTimeoutSec: (record.startupTimeoutSec ?? 30) as number,
      toolTimeoutSec: (record.toolTimeoutSec ?? 600) as number,
    };
  });
  const errors = mcpErrors(servers);
  if (errors.length) throw new Error(errors.join(" "));
  return servers;
}

export function parseTestingLabProfiles(text: string): TestingLabProfile[] {
  let data: unknown;
  try { data = JSON.parse(text); } catch (error) { throw new Error(`Invalid testing profiles JSON: ${errorMessage(error)}`); }
  if (!Array.isArray(data)) throw new Error("Testing profiles must be a JSON array.");
  if (data.length > 32) throw new Error("At most 32 testing profiles can be configured.");
  const ids = new Set<string>();
  return data.map((value, index) => {
    const label = `Testing profile ${index + 1}`, record = object(value, label);
    if (typeof record.id !== "string" || !connectionIdentifier.test(record.id)) throw new Error(`${label}: id must use letters, numbers, underscores, or hyphens.`);
    if (ids.has(record.id.toLowerCase())) throw new Error(`${label}: ids must be unique.`);
    ids.add(record.id.toLowerCase());
    if (typeof record.label !== "string" || !record.label.trim() || utf8Bytes(record.label) > 160) throw new Error(`${label}: a label with at most 160 UTF-8 bytes is required.`);
    if (record.kind !== "virtualbox" && record.kind !== "android") throw new Error(`${label}: kind must be virtualbox or android.`);
    if (typeof record.enabled !== "boolean") throw new Error(`${label}: enabled must be true or false.`);
    if (record.executable !== undefined && typeof record.executable !== "string") throw new Error(`${label}: executable must be text.`);
    const profile: TestingLabProfile = { id: record.id, label: record.label, kind: record.kind,
      enabled: record.enabled, executable: (record.executable as string | undefined) ?? "" };
    for (const key of ["vmName", "deviceSerial", "avdName", "emulatorExecutable", "sdkRoot", "avdHome", "androidUserHome", "guestUser", "passwordEnv"] as const) {
      const item = optionalString(record, key, label);
      profile[key] = item?.trim() ? item : null;
    }
    if ([profile.executable, profile.vmName, profile.deviceSerial, profile.avdName, profile.emulatorExecutable, profile.sdkRoot, profile.avdHome, profile.androidUserHome, profile.guestUser, profile.passwordEnv]
      .some(item => item != null && (utf8Bytes(item) > 4096 || /[\0\r\n]/.test(item)))) throw new Error(`${label}: device fields must be a single line with at most 4,096 UTF-8 bytes.`);
    if (profile.kind === "virtualbox" && !profile.vmName?.trim()) throw new Error(`${label}: an existing VM name is required.`);
    if (profile.passwordEnv && !environmentName.test(profile.passwordEnv)) throw new Error(`${label}: passwordEnv must be an environment variable name.`);
    if (record.emulatorPort != null) {
      if (typeof record.emulatorPort !== "number" || !Number.isInteger(record.emulatorPort) || record.emulatorPort < 5554 || record.emulatorPort > 5682 || record.emulatorPort % 2 !== 0) throw new Error(`${label}: emulatorPort must be an even port from 5554 to 5682.`);
      profile.emulatorPort = record.emulatorPort;
      if (profile.deviceSerial !== `emulator-${profile.emulatorPort}`) throw new Error(`${label}: deviceSerial must match the configured emulator port.`);
    }
    return profile;
  });
}

export function platformNativeAvailable(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
function requireNative(): void {
  if (!platformNativeAvailable()) throw new Error("Saving and device actions require the desktop application. Browser preview changes are not persisted.");
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export async function loadPlatformConfiguration(): Promise<PlatformConfig> {
  await waitForSettingsSaves("agent-platform");
  return platformNativeAvailable() ? invoke<PlatformConfig>("agent_platform_configuration") : defaultPlatformConfiguration();
}
export async function savePlatformConfiguration(configuration: PlatformConfig): Promise<PlatformConfig> {
  requireNative();
  const errors = validatePlatformConfiguration(configuration);
  if (errors.length) throw new Error(errors.join(" "));
  return serializeSettingsSave("agent-platform", async () => {
    const saved = await invoke<PlatformConfig>("agent_platform_save_configuration", { configuration });
    window.dispatchEvent(new CustomEvent<PlatformConfig>(PLATFORM_SETTINGS_EVENT, { detail: saved }));
    return saved;
  });
}
export async function savePlatformConfigurationPatch(settings: PlatformConfigurationPatch): Promise<PlatformConfig> {
  requireNative();
  return serializeSettingsSave("agent-platform", async () => {
    // The native partial-update path merges under CONFIG_WRITE, so independent
    // tool or UI changes are retained instead of overwritten by a stale snapshot.
    await invoke("agent_platform_action", { name: "app_control", args: { action: "set", settings, source: "settings-ui" } });
    const saved = await invoke<PlatformConfig>("agent_platform_configuration");
    window.dispatchEvent(new CustomEvent<PlatformConfig>(PLATFORM_SETTINGS_EVENT, { detail: saved }));
    return saved;
  });
}
export const listPlatformSkills = (): Promise<PlatformSkill[]> => platformNativeAvailable() ? invoke("agent_platform_skills") : Promise.resolve([]);
export const listPlatformPlugins = (): Promise<PlatformPlugin[]> => platformNativeAvailable() ? invoke("agent_platform_plugins") : Promise.resolve([]);
export const searchPlatformActivity = (query = "", limit = 100): Promise<PlatformActivity[]> => platformNativeAvailable()
  ? invoke("agent_platform_activity", { query, limit }) : Promise.resolve([]);
export const searchPlatformMemories = (query = "", limit = 100): Promise<PlatformMemory[]> => platformNativeAvailable()
  ? invoke("agent_platform_memories", { query, limit }) : Promise.resolve([]);
export function executePlatformAction<T = unknown>(name: "app_control" | "agent_memory" | "skill_library", args: Record<string, unknown>): Promise<T> {
  requireNative(); return invoke<T>("agent_platform_action", { name, args });
}
export async function loadTestingLabProfiles(): Promise<TestingLabProfile[]> {
  await waitForSettingsSaves("testing-lab");
  return platformNativeAvailable() ? parseTestingLabProfiles(JSON.stringify(await invoke("testing_lab_profiles"))) : [];
}
export async function saveTestingLabProfiles(profiles: TestingLabProfile[]): Promise<TestingLabProfile[]> {
  requireNative();
  const validated = parseTestingLabProfiles(JSON.stringify(profiles));
  return serializeSettingsSave("testing-lab", async () => {
    await invoke("testing_lab_save_profiles", { profiles: validated });
    // A tool can update the target before the UI receives the save receipt.
    // Read the current source under this queue, rather than normalize to an old receipt.
    return parseTestingLabProfiles(JSON.stringify(await invoke("testing_lab_profiles")));
  });
}
export function executeTestingLabAction(args: TestingLabAction): Promise<unknown> {
  requireNative(); return invoke("testing_lab_action", { args });
}

function colorLuminance(color: string): number {
  const channels = [1, 3, 5].map(offset => {
    const value = parseInt(color.slice(offset, offset + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}
function readableAccent(color: string, light: boolean): string {
  const surface = colorLuminance(light ? "#e9ebf2" : "#142536");
  const rgb = [1, 3, 5].map(offset => parseInt(color.slice(offset, offset + 2), 16));
  for (let step = 0; step <= 64; ++step) {
    const adjusted = `#${rgb.map(channel => Math.round(channel + ((light ? 0 : 255) - channel) * step / 64).toString(16).padStart(2, "0")).join("")}`;
    const ink = colorLuminance(adjusted);
    if ((Math.max(ink, surface) + .05) / (Math.min(ink, surface) + .05) >= 4.5) return adjusted;
  }
  return light ? "#000000" : "#ffffff";
}

export function applyPlatformAppearance(appearance: AppearanceConfig, root: HTMLElement = document.documentElement): void {
  const systemLight = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: light)").matches;
  const theme = appearance.theme === "system" ? (systemLight ? "light" : "dark") : appearance.theme;
  root.dataset.platformTheme = theme;
  root.dataset.platformDensity = appearance.density;
  root.dataset.platformReducedMotion = String(appearance.reducedMotion);
  root.dataset.platformHighContrast = String(appearance.highContrast);
  root.style.colorScheme = theme;
  const font = appearance.fontFamily === "system" ? '"Segoe UI", system-ui, sans-serif'
    : `"${appearance.fontFamily.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}", system-ui, sans-serif`;
  const luminance = colorLuminance(appearance.accentColor);
  const linkColor = readableAccent(appearance.accentColor, theme === "light");
  const variables: Record<string, string> = {
    "--platform-accent": appearance.accentColor, "--blue": appearance.accentColor,
    "--platform-link": linkColor, "--violet": linkColor,
    "--platform-accent-text": luminance > .179 ? "#000000" : "#ffffff",
    "--blue-soft": `${appearance.accentColor}25`, "--platform-font-family": font,
    "--platform-font-size": `${appearance.fontSize}px`, "--chat-font-size": `${appearance.fontSize}px`,
    "--platform-space": appearance.density === "compact" ? "12px" : "20px",
    ...(theme === "light" ? {
      "--bg": "#f4f5f8", "--surface": "#ffffff", "--surface-2": "#f1f2f6", "--surface-3": "#e9ebf2",
      "--text": "#202432", "--muted": "#596477", "--line": "#c6cbd6", "--line-soft": "#e0e3ea",
      "--green": "#116348", "--amber": "#87531a", "--red": "#a72337",
    } : {
      "--bg": "#08121b", "--surface": "#0c1823", "--surface-2": "#101f2c", "--surface-3": "#142536",
      "--text": "#e3edf7", "--muted": "#abb3c0", "--line": "#263b4d", "--line-soft": "#1b2d3c",
      "--green": "#35d38a", "--amber": "#f5ad32", "--red": "#ff646d",
    }),
  };
  if (appearance.highContrast) Object.assign(variables, theme === "light" ? {
    "--bg": "#ffffff", "--surface": "#ffffff", "--surface-2": "#f6f6f6", "--surface-3": "#ececec",
    "--text": "#000000", "--muted": "#242424", "--line": "#111111", "--line-soft": "#696969",
  } : {
    "--bg": "#000000", "--surface": "#000000", "--surface-2": "#101010", "--surface-3": "#202020",
    "--text": "#ffffff", "--muted": "#e2e2e2", "--line": "#ffffff", "--line-soft": "#9f9f9f",
  });
  Object.entries(variables).forEach(([name, value]) => root.style.setProperty(name, value));
}

export function usePlatformConfiguration(initialConfiguration?: PlatformConfig | null) {
  const preview = !platformNativeAvailable();
  const [configuration, setConfiguration] = useState<PlatformConfig | null>(() => initialConfiguration ?? (preview ? defaultPlatformConfiguration() : null));
  const [loading, setLoading] = useState(!preview && !initialConfiguration);
  const [error, setError] = useState("");
  const current = useRef(configuration);
  const generation = useRef(0);
  const mounted = useRef(true);
  const accept = useCallback((value: PlatformConfig) => {
    current.current = value; setConfiguration(value); setError("");
    applyPlatformAppearance(value.appearance);
  }, []);
  const reload = useCallback(async () => {
    const revision = ++generation.current;
    setLoading(true); setError("");
    try {
      const value = await loadPlatformConfiguration();
      if (mounted.current && revision === generation.current) accept(value);
    } catch (cause) { if (mounted.current && revision === generation.current) setError(errorMessage(cause)); }
    finally { if (mounted.current && revision === generation.current) setLoading(false); }
  }, [accept]);
  useEffect(() => {
    mounted.current = true;
    let disposed = false, unlisten: UnlistenFn | undefined;
    const changed = (value: PlatformConfig) => { if (!disposed) { ++generation.current; accept(value); setLoading(false); } };
    const browserChanged = (event: Event) => changed((event as CustomEvent<PlatformConfig>).detail);
    window.addEventListener(PLATFORM_SETTINGS_EVENT, browserChanged);
    if (!preview) {
      void listen<PlatformConfig>(PLATFORM_SETTINGS_EVENT, event => changed(event.payload))
        .then(remove => { if (disposed) remove(); else unlisten = remove; })
        .catch(cause => { if (!disposed) setError(`Settings updates could not be subscribed: ${errorMessage(cause)}`); });
    }
    void reload();
    const query = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: light)") : null;
    const systemChanged = () => { if (current.current?.appearance.theme === "system") applyPlatformAppearance(current.current.appearance); };
    query?.addEventListener?.("change", systemChanged);
    return () => {
      disposed = true; mounted.current = false; ++generation.current;
      window.removeEventListener(PLATFORM_SETTINGS_EVENT, browserChanged); unlisten?.();
      query?.removeEventListener?.("change", systemChanged);
    };
  }, [accept, preview, reload]);
  // Writers publish their source result to the listener above. Accepting the
  // same result again after await could replace a newer native tool event.
  const save = useCallback(savePlatformConfiguration, []);
  const savePatch = useCallback(savePlatformConfigurationPatch, []);
  const getCurrentConfiguration = useCallback(() => current.current, []);
  return { configuration, loading, error, preview, reload, save, savePatch, getCurrentConfiguration };
}
