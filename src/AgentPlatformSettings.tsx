import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { BookOpen, Boxes, CheckCheck, Database, FlaskConical, History, Palette, Plug, Save, SlidersHorizontal, Sparkles } from "lucide-react";
import { openLocalPath } from "./api";
import { listen } from "@tauri-apps/api/event";
import {
  applyPlatformAppearance, defaultPlatformConfiguration, directoryLines, errorMessage,
  executePlatformAction, executeTestingLabAction, listPlatformPlugins, listPlatformSkills,
  loadTestingLabProfiles, parseMcpServers, parseTestingLabProfiles, saveTestingLabProfiles,
  searchPlatformActivity, searchPlatformMemories, usePlatformConfiguration,
  validatePlatformConfiguration, VERIFICATION_OPTIONS,
  type AppearanceConfig, type McpServer, type PlatformActivity, type PlatformConfig,
  type PlatformMemory, type PlatformPlugin, type PlatformSkill, type TestingLabAction, type TestingLabProfile,
} from "./agent-platform";
import "./agent-platform.css";

function Panel({ id, title, icon, children, wide = false }: { id: string; title: string; icon: ReactNode; children: ReactNode; wide?: boolean }) {
  return <section className={`platform-panel${wide ? " platform-panel-wide" : ""}`} role="region" aria-labelledby={`${id}-title`}>
    <h2 id={`${id}-title`}>{icon}{title}</h2>{children}
  </section>;
}
function Toggle({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled?: boolean; onChange: (enabled: boolean) => void }) {
  return <label className="platform-toggle"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /><span>{label}</span></label>;
}
function formatTime(value: string): string {
  const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
function changedDisabled(ids: string[], id: string, enabled: boolean): string[] {
  return enabled ? ids.filter(value => value !== id) : [...new Set([...ids, id])];
}
function screenshotPath(receipt: unknown): string | null {
  const value = receipt && typeof receipt === "object" ? (receipt as Record<string, unknown>).imagePath : null;
  return typeof value === "string" && /^(?:[A-Za-z]:[\\/]|\\\\|\/)/.test(value) ? value : null;
}
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const pluginExample = {
  id: "example-tools", name: "Example Tools", description: "Portable skill package", version: "1.0.0",
  skills: ["skills"], mcpServers: [],
};

export function AgentPlatformSettings({ onConfigurationChange }: { onConfigurationChange?: (configuration: PlatformConfig) => void } = {}) {
  const platform = usePlatformConfiguration();
  const [draft, setDraft] = useState<PlatformConfig | null>(null);
  const [skillDirectoryText, setSkillDirectoryText] = useState("");
  const [pluginDirectoryText, setPluginDirectoryText] = useState("");
  const [mcpText, setMcpText] = useState("[]");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const callback = useRef(onConfigurationChange);
  callback.current = onConfigurationChange;
  const savedAppearance = useRef(platform.configuration?.appearance);

  const [skills, setSkills] = useState<PlatformSkill[]>([]);
  const [plugins, setPlugins] = useState<PlatformPlugin[]>([]);
  const [skillSearch, setSkillSearch] = useState("");
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [skillError, setSkillError] = useState("");
  const [pluginError, setPluginError] = useState("");
  const [skillBodies, setSkillBodies] = useState<Record<string, string>>({});
  const [readingSkills, setReadingSkills] = useState<string[]>([]);

  const [activity, setActivity] = useState<PlatformActivity[]>([]);
  const [activityQuery, setActivityQuery] = useState("");
  const [activityLoading, setActivityLoading] = useState(false);
  const [activityError, setActivityError] = useState("");
  const [memories, setMemories] = useState<PlatformMemory[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState("");
  const [memorySaving, setMemorySaving] = useState(false);
  const [memoryDraft, setMemoryDraft] = useState({ key: "", kind: "fact" as "fact" | "lesson", content: "", source: "", scope: "global", evidence: "" });
  const [deleteTarget, setDeleteTarget] = useState<{ kind: "activity" | "memory"; id: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const activitySearch = useRef("");
  const memorySearch = useRef("");
  const libraryRequest = useRef(0);
  const activityRequest = useRef(0);
  const memoryRequest = useRef(0);

  const [labProfiles, setLabProfiles] = useState<TestingLabProfile[]>([]);
  const [labText, setLabText] = useState("[]");
  const [labLoading, setLabLoading] = useState(true);
  const [labSaving, setLabSaving] = useState(false);
  const [labError, setLabError] = useState("");
  const [labNotice, setLabNotice] = useState("");
  const [labBusy, setLabBusy] = useState<string | null>(null);
  const [labReceipts, setLabReceipts] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!platform.configuration) return;
    setDraft(platform.configuration);
    setSkillDirectoryText(platform.configuration.skillDirectories.join("\n"));
    setPluginDirectoryText(platform.configuration.pluginDirectories.join("\n"));
    setMcpText(pretty(platform.configuration.mcpServers));
    savedAppearance.current = platform.configuration.appearance;
    callback.current?.(platform.configuration);
  }, [platform.configuration]);
  useEffect(() => {
    if (draft && !validatePlatformConfiguration({ ...defaultPlatformConfiguration(), appearance: draft.appearance }).length)
      applyPlatformAppearance(draft.appearance);
  }, [draft?.appearance]);
  useEffect(() => () => { if (savedAppearance.current) applyPlatformAppearance(savedAppearance.current); }, []);

  const refreshLibrary = useCallback(async () => {
    const request = ++libraryRequest.current;
    setLibraryLoading(true);
    const [skillResult, pluginResult] = await Promise.allSettled([listPlatformSkills(), listPlatformPlugins()]);
    if (request !== libraryRequest.current) return;
    if (skillResult.status === "fulfilled") { setSkills(skillResult.value); setSkillError(""); }
    else setSkillError(errorMessage(skillResult.reason));
    if (pluginResult.status === "fulfilled") { setPlugins(pluginResult.value); setPluginError(""); }
    else setPluginError(errorMessage(pluginResult.reason));
    setLibraryLoading(false);
  }, []);
  const refreshActivity = useCallback(async (query = "") => {
    const request = ++activityRequest.current;
    activitySearch.current = query;
    setActivityLoading(true); setActivityError("");
    try { const records = await searchPlatformActivity(query); if (request === activityRequest.current) setActivity(records); }
    catch (cause) { if (request === activityRequest.current) setActivityError(errorMessage(cause)); }
    finally { if (request === activityRequest.current) setActivityLoading(false); }
  }, []);
  const refreshMemories = useCallback(async (query = "") => {
    const request = ++memoryRequest.current;
    memorySearch.current = query;
    setMemoryLoading(true); setMemoryError("");
    try { const records = await searchPlatformMemories(query); if (request === memoryRequest.current) setMemories(records); }
    catch (cause) { if (request === memoryRequest.current) setMemoryError(errorMessage(cause)); }
    finally { if (request === memoryRequest.current) setMemoryLoading(false); }
  }, []);
  useEffect(() => {
    if (!platform.configuration) return;
    void refreshLibrary(); void refreshActivity(activitySearch.current); void refreshMemories(memorySearch.current);
  }, [platform.configuration, refreshLibrary, refreshActivity, refreshMemories]);
  useEffect(() => () => { ++libraryRequest.current; ++activityRequest.current; ++memoryRequest.current; }, []);
  useEffect(() => {
    let active = true, changed = false;
    let unlisten: (() => void) | undefined;
    void listen<TestingLabProfile[]>("opencore-testing-profiles-changed", ({ payload }) => {
      if (!active) return;
      changed = true;
      try { const profiles = parseTestingLabProfiles(pretty(payload)); setLabProfiles(profiles); setLabText(pretty(profiles)); setLabError(""); setLabNotice("Testing profiles refreshed from the app."); }
      catch (cause) { setLabError(errorMessage(cause)); }
    }).then(stop => { if (active) unlisten = stop; else stop(); }).catch(() => {});
    loadTestingLabProfiles().then(profiles => { if (active && !changed) { setLabProfiles(profiles); setLabText(pretty(profiles)); } })
      .catch(cause => { if (active) setLabError(errorMessage(cause)); })
      .finally(() => { if (active) setLabLoading(false); });
    return () => { active = false; unlisten?.(); };
  }, []);

  const mcp = useMemo(() => {
    try { return { servers: parseMcpServers(mcpText), error: "" }; }
    catch (cause) { return { servers: null, error: errorMessage(cause) }; }
  }, [mcpText]);
  const lab = useMemo(() => {
    try { return { profiles: parseTestingLabProfiles(labText), error: "" }; }
    catch (cause) { return { profiles: null, error: errorMessage(cause) }; }
  }, [labText]);
  const effectiveDraft = draft ? { ...draft, mcpServers: mcp.servers ?? draft.mcpServers } : null;
  const validation = effectiveDraft ? validatePlatformConfiguration(effectiveDraft) : [];
  const labDirty = lab.profiles ? pretty(lab.profiles) !== pretty(labProfiles) : true;
  const dirty = effectiveDraft && pretty(effectiveDraft) !== pretty(platform.configuration);

  function update<K extends keyof PlatformConfig>(key: K, value: PlatformConfig[K]) {
    setDraft(previous => previous ? { ...previous, [key]: value } : previous);
    setNotice(""); setSaveError("");
  }
  function appearance(value: Partial<AppearanceConfig>) {
    if (draft) update("appearance", { ...draft.appearance, ...value });
  }
  async function save() {
    if (!effectiveDraft || mcp.error || validation.length) return;
    setSaving(true); setSaveError(""); setNotice("");
    try { await platform.save(effectiveDraft); setNotice("Settings saved on this computer."); }
    catch (cause) { setSaveError(`Could not save settings: ${errorMessage(cause)}`); }
    finally { setSaving(false); }
  }
  async function readSkill(skill: PlatformSkill) {
    setReadingSkills(previous => [...previous, skill.id]); setSkillError("");
    try {
      const result = await executePlatformAction<{ skill: PlatformSkill; content: string }>("skill_library", { action: "read", id: skill.id });
      setSkillBodies(previous => ({ ...previous, [skill.id]: result.content }));
    } catch (cause) { setSkillError(`Could not read ${skill.name}: ${errorMessage(cause)}`); }
    finally { setReadingSkills(previous => previous.filter(id => id !== skill.id)); }
  }
  async function deleteRecord(kind: "activity" | "memory", id: string) {
    setDeleting(true);
    try {
      await executePlatformAction(kind === "activity" ? "app_control" : "agent_memory", { action: kind === "activity" ? "delete_activity" : "delete", id, source: "settings UI" });
      setDeleteTarget(null);
      if (kind === "activity") await refreshActivity(activitySearch.current); else await refreshMemories(memorySearch.current);
    } catch (cause) { if (kind === "activity") setActivityError(errorMessage(cause)); else setMemoryError(errorMessage(cause)); }
    finally { setDeleting(false); }
  }
  async function recordMemory() {
    setMemorySaving(true); setMemoryError("");
    try {
      await executePlatformAction("agent_memory", { action: "record", ...memoryDraft, evidence: memoryDraft.evidence || null });
      setMemoryDraft({ key: "", kind: "fact", content: "", source: "", scope: "global", evidence: "" });
      await refreshMemories(memorySearch.current);
    } catch (cause) { setMemoryError(errorMessage(cause)); }
    finally { setMemorySaving(false); }
  }
  function addMcp(kind: "stdio" | "http") {
    if (!mcp.servers) return;
    let count = mcp.servers.length + 1;
    while (mcp.servers.some(server => server.id === `server-${count}` || server.name === `Server-${count}`)) ++count;
    const server: McpServer = {
      id: `server-${count}`, name: `Server-${count}`, enabled: false,
      command: kind === "stdio" ? "node" : null, args: kind === "stdio" ? ["C:/tools/mcp-server.js"] : [],
      env: {}, url: kind === "http" ? "https://example.com/mcp" : null,
      bearerTokenEnvVar: kind === "http" ? "MCP_TOKEN" : null, startupTimeoutSec: 30, toolTimeoutSec: 600,
    };
    setMcpText(pretty([...mcp.servers, server])); setNotice("");
  }
  function addLab(kind: "virtualbox" | "android") {
    if (!lab.profiles) return;
    let count = lab.profiles.length + 1;
    while (lab.profiles.some(profile => profile.id === `device-${count}`)) ++count;
    const profile: TestingLabProfile = { id: `device-${count}`, label: kind === "virtualbox" ? "Existing Windows VM" : "Existing Android device",
      kind, enabled: false, executable: kind === "virtualbox" ? "VBoxManage" : "adb",
      ...(kind === "virtualbox" ? { vmName: "Your existing VM name", guestUser: "", passwordEnv: "" } : { deviceSerial: "", avdName: "", emulatorExecutable: "" }) };
    setLabText(pretty([...lab.profiles, profile])); setLabNotice("");
  }
  async function saveLabs() {
    if (!lab.profiles) return;
    setLabSaving(true); setLabError(""); setLabNotice("");
    try { const profiles = await saveTestingLabProfiles(lab.profiles); setLabProfiles(profiles); setLabText(pretty(profiles)); setLabNotice("Testing profiles saved on this computer."); }
    catch (cause) { setLabError(`Could not save testing profiles: ${errorMessage(cause)}`); }
    finally { setLabSaving(false); }
  }
  async function runLab(profile: TestingLabProfile, action: TestingLabAction["action"]) {
    setLabBusy(profile.id); setLabError("");
    try { const receipt = await executeTestingLabAction({ action, profileId: profile.id }); setLabReceipts(previous => ({ ...previous, [profile.id]: receipt })); }
    catch (cause) { setLabError(`${profile.label}: ${errorMessage(cause)}`); }
    finally { setLabBusy(null); }
  }

  if (!draft) return <div className="agent-platform-settings platform-load-state" aria-busy={platform.loading}>
    <h2>Agent settings</h2>
    {platform.loading ? <p role="status">Loading settings from this computer…</p> : <><p role="alert">Could not load settings: {platform.error}</p><button onClick={() => void platform.reload()}>Retry loading settings</button></>}
  </div>;

  const visibleSkills = skills.filter(skill => `${skill.name} ${skill.description} ${skill.source}`.toLowerCase().includes(skillSearch.toLowerCase()));
  const locked = saving || platform.loading;
  return <div className="agent-platform-settings">
    <header className="platform-settings-header">
      <div><h1><SlidersHorizontal size={25} /> Agent settings</h1><p>OpenCore’s instructions, tools, appearance, and sourced memory.</p></div>
      <div className="platform-save-controls"><span>{platform.preview ? "Preview defaults" : dirty ? "Unsaved changes" : "Source configuration loaded"}</span>
        <button className="platform-primary" disabled={locked || platform.preview || Boolean(mcp.error) || validation.length > 0} onClick={() => void save()}><Save size={16} />{saving ? "Saving settings…" : "Save settings"}</button>
      </div>
    </header>
    {platform.preview && <p className="platform-banner" role="status">Browser preview — settings and device actions are not persisted. Open the desktop application to save.</p>}
    {platform.error && <p className="platform-error" role="alert">{platform.error}</p>}
    {saveError && <p className="platform-error" role="alert">{saveError}</p>}
    {notice && <p className="platform-success" role="status">{notice}</p>}
    {validation.length > 0 && <div className="platform-error" role="alert"><strong>Correct these settings before saving:</strong><ul>{validation.map(value => <li key={value}>{value}</li>)}</ul></div>}

    <div className="platform-panels">
      <Panel id="platform-identity" title="Identity and instructions" icon={<Sparkles size={19} />}>
        <div className="platform-identity"><strong>OpenCore</strong><span>Default assistant identity</span></div>
        <p className="platform-note">Your instructions are added to OpenCore’s built-in instructions. Use them for working preferences, project conventions, and the evidence you expect.</p>
        <label className="platform-field" htmlFor="platform-prompt">Additional system instructions</label>
        <textarea id="platform-prompt" rows={7} value={draft.systemPrompt} disabled={locked} onChange={event => update("systemPrompt", event.target.value)} placeholder="For example: Explain important decisions and keep commands reproducible." />
        <p className="platform-note">{new TextEncoder().encode(draft.systemPrompt).length.toLocaleString()} / 32,768 UTF-8 bytes · saved instructions apply to subsequent agent work.</p>
      </Panel>

      <Panel id="platform-verification" title="Verification and repair" icon={<CheckCheck size={19} />}>
        <fieldset className="platform-choice-fieldset"><legend>Additional verification</legend><div className="platform-choices">
          {VERIFICATION_OPTIONS.map(option => <label key={option.value} className={draft.verification === option.value ? "selected" : ""}>
            <input type="radio" name="platform-verification" value={option.value} checked={draft.verification === option.value} disabled={locked} onChange={() => update("verification", option.value)} /><span>{option.label}</span>
          </label>)}
        </div></fieldset>
        <p className="platform-note">{VERIFICATION_OPTIONS.find(option => option.value === draft.verification)?.description}</p>
        <label className="platform-field" htmlFor="platform-repairs">Repair attempts</label>
        <input id="platform-repairs" type="number" min={0} max={10} step={1} value={draft.repairAttempts} disabled={locked} onChange={event => update("repairAttempts", Number(event.target.value))} />
        <p className="platform-note">Suggested correction attempts per defect after review. This guides the agent; it is not a hard execution limit. Zero asks for review without added repairs. Studio handoffs release the text model for the GPU queue.</p>
      </Panel>

      <Panel id="platform-context" title="Context and compaction" icon={<SlidersHorizontal size={19} />}>
        <label className="platform-field" htmlFor="platform-compaction">Auto-compaction trigger (tokens)</label>
        <input id="platform-compaction" type="number" min={1024} max={3000000} step={1} value={draft.compactAtTokens} disabled={locked} onChange={event => update("compactAtTokens", Number(event.target.value))} />
        <p className="platform-note">Requested trigger: {draft.compactAtTokens.toLocaleString()} tokens. The loaded model’s usable window and response reserve determine the effective trigger. ECHO keeps exact archived history separately.</p>
        <p className="platform-note">The same source configuration is used by the settings UI and OpenCore’s structured settings tools.</p>
      </Panel>

      <Panel id="platform-appearance" title="Appearance" icon={<Palette size={19} />}>
        <fieldset className="platform-choice-fieldset"><legend>Theme</legend><div className="platform-choices">
          {(["dark", "light", "system"] as const).map(theme => <label key={theme} className={draft.appearance.theme === theme ? "selected" : ""}>
            <input type="radio" name="platform-theme" checked={draft.appearance.theme === theme} disabled={locked} onChange={() => appearance({ theme })} /><span>{theme[0].toUpperCase() + theme.slice(1)}</span>
          </label>)}
        </div></fieldset>
        <div className="platform-form-grid">
          <div><label className="platform-field" htmlFor="platform-accent">Accent color</label><div className="platform-color-field">
            <input aria-label="Pick accent color" type="color" value={/^#[0-9a-fA-F]{6}$/.test(draft.appearance.accentColor) ? draft.appearance.accentColor : "#7c5cff"} disabled={locked} onChange={event => appearance({ accentColor: event.target.value })} />
            <input id="platform-accent" value={draft.appearance.accentColor} disabled={locked} spellCheck={false} maxLength={7} onChange={event => appearance({ accentColor: event.target.value })} />
          </div></div>
          <div><label className="platform-field" htmlFor="platform-text-size">Text size (pixels)</label><input id="platform-text-size" type="number" min={10} max={24} step={1} value={draft.appearance.fontSize} disabled={locked} onChange={event => appearance({ fontSize: Number(event.target.value) })} /></div>
        </div>
        <label className="platform-field" htmlFor="platform-font">Font family</label><input id="platform-font" list="platform-fonts" value={draft.appearance.fontFamily} disabled={locked} onChange={event => appearance({ fontFamily: event.target.value })} /><datalist id="platform-fonts"><option value="system" /><option value="Segoe UI" /><option value="Arial" /><option value="Cascadia Code" /><option value="Consolas" /></datalist>
        <fieldset className="platform-choice-fieldset"><legend>Spacing</legend><div className="platform-choices">
          {(["comfortable", "compact"] as const).map(density => <label key={density} className={draft.appearance.density === density ? "selected" : ""}><input type="radio" name="platform-density" checked={draft.appearance.density === density} disabled={locked} onChange={() => appearance({ density })} /><span>{density[0].toUpperCase() + density.slice(1)}</span></label>)}
        </div></fieldset>
        <Toggle label="Reduce motion" checked={draft.appearance.reducedMotion} disabled={locked} onChange={reducedMotion => appearance({ reducedMotion })} />
        <Toggle label="High contrast" checked={draft.appearance.highContrast} disabled={locked} onChange={highContrast => appearance({ highContrast })} />
        <div className="platform-appearance-preview">Readable text <span>Accent and spacing preview</span></div><p className="platform-note">Valid changes preview immediately. Save settings to keep them after restarting.</p>
      </Panel>

      <Panel id="platform-skills" title="Skills" icon={<BookOpen size={19} />}>
        <p className="platform-note">Built-in development skills and custom SKILL.md files share this library. Names and descriptions are listed first; instructions are read only when requested.</p>
        <label className="platform-field" htmlFor="platform-skill-directories">Custom skill directories (one absolute path per line)</label>
        <textarea id="platform-skill-directories" rows={3} value={skillDirectoryText} disabled={locked} spellCheck={false} onChange={event => { setSkillDirectoryText(event.target.value); update("skillDirectories", directoryLines(event.target.value)); }} placeholder="C:\Users\you\skills" />
        <div className="platform-search-row"><label className="platform-sr-only" htmlFor="platform-skill-search">Filter skills</label><input id="platform-skill-search" type="search" value={skillSearch} onChange={event => setSkillSearch(event.target.value)} placeholder="Filter names and descriptions" /><button disabled={libraryLoading || platform.preview} onClick={() => void refreshLibrary()}>{libraryLoading ? "Refreshing…" : "Refresh library"}</button></div>
        {skillError && <p className="platform-error" role="alert">{skillError}</p>}
        <div className="platform-records">{visibleSkills.map(skill => <article className="platform-record" key={skill.id}>
          <div className="platform-record-title"><strong>{skill.name}</strong><span className="platform-badge">{skill.source}</span></div><p>{skill.description}</p>
          <Toggle label={`Enable ${skill.name}`} checked={!draft.disabledSkills.includes(skill.id) && (!skill.pluginId || !draft.disabledPlugins.includes(skill.pluginId))} disabled={locked || Boolean(skill.pluginId && draft.disabledPlugins.includes(skill.pluginId))} onChange={enabled => update("disabledSkills", changedDisabled(draft.disabledSkills, skill.id, enabled))} />
          {skill.path && <code className="platform-path">{skill.path}</code>}
          <button disabled={platform.preview || readingSkills.includes(skill.id)} onClick={() => void readSkill(skill)} aria-label={`Read ${skill.name} instructions`}>{readingSkills.includes(skill.id) ? "Reading instructions…" : "Read instructions"}</button>
          {skillBodies[skill.id] !== undefined && <details open><summary>Loaded instructions</summary><pre className="platform-code">{skillBodies[skill.id]}</pre></details>}
        </article>)}{!visibleSkills.length && <p className="platform-empty">{platform.preview ? "The desktop application discovers your built-in and custom skills." : libraryLoading ? "Discovering skills…" : "No skills match this filter. Save custom directories, then refresh the library."}</p>}</div>
      </Panel>

      <Panel id="platform-plugins" title="Portable plugins" icon={<Boxes size={19} />}>
        <p className="platform-note">Add directories containing portable plugin folders. Discovery reads manifests and confined skill paths. Enabled plugin MCP servers are supplied to the next agent session; discovery does not run installation hooks.</p>
        <label className="platform-field" htmlFor="platform-plugin-directories">Plugin directories (one absolute path per line)</label><textarea id="platform-plugin-directories" rows={3} value={pluginDirectoryText} disabled={locked} spellCheck={false} onChange={event => { setPluginDirectoryText(event.target.value); update("pluginDirectories", directoryLines(event.target.value)); }} placeholder="C:\Users\you\plugins" />
        {pluginError && <p className="platform-error" role="alert">{pluginError}</p>}
        <div className="platform-records">{plugins.map(plugin => <article className="platform-record" key={plugin.id}>
          <div className="platform-record-title"><strong>{plugin.name}</strong>{plugin.version && <span className="platform-badge">{plugin.version}</span>}</div><p>{plugin.description}</p>
          <Toggle label={`Enable ${plugin.name}`} checked={!draft.disabledPlugins.includes(plugin.id)} disabled={locked} onChange={enabled => update("disabledPlugins", changedDisabled(draft.disabledPlugins, plugin.id, enabled))} />
          <code className="platform-path">{plugin.path}</code><p className="platform-note">{plugin.skills.length} skills · {plugin.mcpServers.length} MCP servers{plugin.mcpServers.length ? `: ${plugin.mcpServers.join(", ")}` : ""}</p>
          {plugin.warnings.map((warning, index) => <p className="platform-error" key={index}>{warning}</p>)}
        </article>)}{!plugins.length && <p className="platform-empty">{platform.preview ? "Open the desktop application to discover local plugin folders." : libraryLoading ? "Discovering plugins…" : "No portable plugins discovered. Save a plugin directory to populate this list."}</p>}</div>
        <details><summary>Portable plugin example</summary><p className="platform-note">Place opencore-plugin.json at the plugin root and SKILL.md files under skills/. Paths in the manifest stay within that plugin folder.</p><pre className="platform-code">{pretty(pluginExample)}</pre></details>
      </Panel>

      <Panel id="platform-mcp" title="MCP connections" icon={<Plug size={19} />} wide>
        <p className="platform-note">Configure stdio commands or HTTP endpoints. Each profile needs a unique id and name, an enabled switch, and timeouts. For stdio, env can include local secrets. For HTTP, bearerTokenEnvVar references an existing environment variable for authorization. Agent tools receive redacted configuration.</p>
        <div className="platform-toolbar"><button disabled={locked || Boolean(mcp.error)} onClick={() => addMcp("stdio")}>Add stdio example</button><button disabled={locked || Boolean(mcp.error)} onClick={() => addMcp("http")}>Add HTTP example</button><span>{mcp.servers?.filter(server => server.enabled).length ?? 0} enabled profiles</span></div>
        <label className="platform-field" htmlFor="platform-mcp-json">MCP profiles JSON</label><textarea id="platform-mcp-json" className="platform-json-editor" rows={12} value={mcpText} spellCheck={false} disabled={locked} aria-invalid={Boolean(mcp.error)} aria-describedby="platform-mcp-help" onChange={event => { setMcpText(event.target.value); setNotice(""); setSaveError(""); }} />
        {mcp.error && <p className="platform-error" role="alert">{mcp.error}</p>}
        <p id="platform-mcp-help" className="platform-note">Examples start disabled. Stdio uses command, args, and env; HTTP uses url and bearerTokenEnvVar with empty args/env. Edit startupTimeoutSec (1–600) and toolTimeoutSec (1–3,600), then enable and save. Local stdio secret values are visible in this editor.</p>
        {mcp.servers && <div className="platform-connection-summaries">{mcp.servers.map(server => <div key={server.id}><strong>{server.name}</strong><span>{server.command ? "stdio" : "HTTP"} · {server.enabled ? "Enabled" : "Disabled"}</span><code>{server.command ?? server.url}</code></div>)}</div>}
      </Panel>

      <Panel id="platform-activity" title="Activity history" icon={<History size={19} />}>
        <Toggle label="Record app activity" checked={draft.activityEnabled} disabled={locked} onChange={enabled => update("activityEnabled", enabled)} />
        <p className="platform-note">Search indexed records of actual settings changes and studio work. Recording changes take effect after Save settings.</p>
        <form className="platform-search-row" onSubmit={event => { event.preventDefault(); void refreshActivity(activityQuery); }}><label className="platform-sr-only" htmlFor="platform-activity-search">Search activity history</label><input id="platform-activity-search" type="search" placeholder="Search activity" value={activityQuery} onChange={event => setActivityQuery(event.target.value)} /><button type="submit" disabled={activityLoading || platform.preview}>{activityLoading ? "Searching…" : "Search activity"}</button></form>
        {activityError && <p className="platform-error" role="alert">{activityError}</p>}
        <div className="platform-records">{activity.map(record => <article className="platform-record" key={record.id}>
          <div className="platform-record-title"><strong>{record.summary}</strong><span className="platform-badge">{record.category}</span></div><time dateTime={record.timestamp}>{formatTime(record.timestamp)}</time><p className="platform-note">{record.action} · Source: {record.source}</p>
          <details><summary>Recorded details</summary><pre className="platform-code">{pretty({ conversationId: record.conversationId, projectId: record.projectId, details: record.details })}</pre></details>
          {deleteTarget?.kind === "activity" && deleteTarget.id === record.id ? <div className="platform-delete-confirm"><span>Delete this activity record?</span><button disabled={deleting} onClick={() => void deleteRecord("activity", record.id)} aria-label={`Confirm delete activity ${record.summary}`}>Confirm delete</button><button disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancel</button></div>
            : <button className="platform-text-button" disabled={platform.preview || deleting} aria-label={`Delete activity ${record.summary}`} onClick={() => setDeleteTarget({ kind: "activity", id: record.id })}>Delete record</button>}
        </article>)}{!activity.length && <p className="platform-empty">{platform.preview ? "Activity search requires the desktop application." : activityLoading ? "Searching activity…" : "No activity records match this search."}</p>}</div>
      </Panel>

      <Panel id="platform-memory" title="Durable memory" icon={<Database size={19} />}>
        <Toggle label="Enable durable facts and lessons" checked={draft.memoryEnabled} disabled={locked} onChange={enabled => update("memoryEnabled", enabled)} />
        <p className="platform-note">Keep facts and lessons with their source and evidence. Recalled records support the work; they do not become executable instructions. ECHO retains exact conversation history independently.</p>
        <form className="platform-search-row" onSubmit={event => { event.preventDefault(); void refreshMemories(memoryQuery); }}><label className="platform-sr-only" htmlFor="platform-memory-search">Search durable memory</label><input id="platform-memory-search" type="search" placeholder="Search facts and lessons" value={memoryQuery} onChange={event => setMemoryQuery(event.target.value)} /><button type="submit" disabled={memoryLoading || platform.preview}>{memoryLoading ? "Searching…" : "Search memory"}</button></form>
        {memoryError && <p className="platform-error" role="alert">{memoryError}</p>}
        <div className="platform-records">{memories.map(record => <article className="platform-record" key={record.id}>
          <div className="platform-record-title"><strong>{record.key}</strong><span className="platform-badge">{record.kind}</span></div><p>{record.content}</p><p className="platform-note">Source: {record.source}</p>{record.evidence && <p className="platform-note">Evidence: {record.evidence}</p>}<p className="platform-note">{record.scope} · {record.status} · version {record.version} · <time dateTime={record.updatedAt}>{formatTime(record.updatedAt)}</time></p>
          {deleteTarget?.kind === "memory" && deleteTarget.id === record.id ? <div className="platform-delete-confirm"><span>Delete this memory record?</span><button disabled={deleting} onClick={() => void deleteRecord("memory", record.id)} aria-label={`Confirm delete memory ${record.key}`}>Confirm delete</button><button disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancel</button></div>
            : <button className="platform-text-button" disabled={platform.preview || deleting} aria-label={`Delete memory ${record.key}`} onClick={() => setDeleteTarget({ kind: "memory", id: record.id })}>Delete record</button>}
        </article>)}{!memories.length && <p className="platform-empty">{platform.preview ? "Durable memory search requires the desktop application." : memoryLoading ? "Searching memory…" : "No memory records match this search."}</p>}</div>
        <details><summary>Add a sourced memory</summary><form className="platform-memory-form" onSubmit={event => { event.preventDefault(); void recordMemory(); }}>
          <label className="platform-field" htmlFor="platform-memory-key">Memory key</label><input id="platform-memory-key" value={memoryDraft.key} required disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, key: event.target.value })} />
          <div className="platform-form-grid"><div><label className="platform-field" htmlFor="platform-memory-kind">Memory type</label><select id="platform-memory-kind" value={memoryDraft.kind} disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, kind: event.target.value as "fact" | "lesson" })}><option value="fact">Fact</option><option value="lesson">Lesson</option></select></div><div><label className="platform-field" htmlFor="platform-memory-scope">Memory scope</label><input id="platform-memory-scope" value={memoryDraft.scope} required disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, scope: event.target.value })} /></div></div>
          <label className="platform-field" htmlFor="platform-memory-content">Fact or lesson</label><textarea id="platform-memory-content" rows={3} value={memoryDraft.content} required disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, content: event.target.value })} />
          <label className="platform-field" htmlFor="platform-memory-source">Memory source</label><input id="platform-memory-source" value={memoryDraft.source} required disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, source: event.target.value })} />
          <label className="platform-field" htmlFor="platform-memory-evidence">Memory evidence (optional)</label><input id="platform-memory-evidence" value={memoryDraft.evidence} disabled={memorySaving || platform.preview} onChange={event => setMemoryDraft({ ...memoryDraft, evidence: event.target.value })} />
          <button type="submit" disabled={memorySaving || platform.preview || !memoryDraft.key.trim() || !memoryDraft.content.trim() || !memoryDraft.source.trim()}>{memorySaving ? "Saving memory…" : "Save memory record"}</button>
        </form></details>
      </Panel>

      <Panel id="platform-testing" title="Testing lab" icon={<FlaskConical size={19} />} wide>
        <p className="platform-note">Connect existing VirtualBox PCs or Android devices and emulators. Set VBoxManage or adb in executable, and the VM name or device serial. An AVD also needs an installed emulator executable. VM images and mobile SDKs must already be installed.</p>
        <div className="platform-toolbar"><button disabled={labLoading || labSaving || Boolean(lab.error)} onClick={() => addLab("virtualbox")}>Add VirtualBox example</button><button disabled={labLoading || labSaving || Boolean(lab.error)} onClick={() => addLab("android")}>Add Android example</button></div>
        <label className="platform-field" htmlFor="platform-lab-json">Testing profiles JSON</label><textarea id="platform-lab-json" className="platform-json-editor" rows={8} value={labText} spellCheck={false} disabled={labLoading || labSaving} aria-invalid={Boolean(lab.error)} onChange={event => { setLabText(event.target.value); setLabNotice(""); }} />
        {lab.error && <p className="platform-error" role="alert">{lab.error}</p>}{labError && <p className="platform-error" role="alert">{labError}</p>}
        <div className="platform-toolbar"><button className="platform-primary" disabled={platform.preview || labLoading || labSaving || !lab.profiles} onClick={() => void saveLabs()}>{labSaving ? "Saving testing profiles…" : "Save testing profiles"}</button>{labDirty && <span>Save profile edits before running actions.</span>}</div>
        {labNotice && <p className="platform-success" role="status">{labNotice}</p>}
        <div className="platform-lab-devices">{labProfiles.map(profile => <article className="platform-record" key={profile.id}>
          <div className="platform-record-title"><strong>{profile.label}</strong><span className="platform-badge">{profile.kind === "virtualbox" ? "VirtualBox" : "Android"}</span></div><p className="platform-note">{profile.enabled ? "Enabled" : "Disabled"} · {profile.vmName || profile.deviceSerial || profile.avdName || "Default ADB device"}</p>
          <div className="platform-toolbar">{(["status", "inspect", "start", "stop", "screenshot"] as const).map(action => <button key={action} disabled={platform.preview || !profile.enabled || labDirty || labBusy !== null || labSaving || (profile.kind === "android" && ((action === "start" && !profile.avdName) || (action === "stop" && !profile.avdName && !profile.deviceSerial?.startsWith("emulator-"))))} title={profile.kind === "android" && action === "start" && !profile.avdName ? "Configure an existing AVD to start an emulator. Connected devices use Status." : undefined} onClick={() => void runLab(profile, action)} aria-label={`${action[0].toUpperCase() + action.slice(1)} ${profile.label}`}>{labBusy === profile.id ? "Working…" : action[0].toUpperCase() + action.slice(1)}</button>)}</div>
          {labReceipts[profile.id] !== undefined && <details open><summary>Device action receipt</summary><pre className="platform-code">{pretty(labReceipts[profile.id])}</pre>{screenshotPath(labReceipts[profile.id]) && <button disabled={platform.preview} aria-label={`Open screenshot ${profile.label}`} onClick={() => void openLocalPath(screenshotPath(labReceipts[profile.id])!).catch(cause => setLabError(`Could not open screenshot: ${errorMessage(cause)}`))}>Open screenshot</button>}</details>}
        </article>)}{!labProfiles.length && <p className="platform-empty">{platform.preview ? "Device actions require the desktop application." : labLoading ? "Loading testing profiles…" : "No testing devices configured. Add an example, update it for your existing device, and save."}</p>}</div>
      </Panel>
    </div>
    <footer className="platform-settings-footer"><span>Settings are stored in OpenCore’s local source configuration.</span><button disabled={locked || platform.preview} onClick={() => { setNotice(""); setSaveError(""); void platform.reload(); }}>Reload saved settings</button></footer>
  </div>;
}
