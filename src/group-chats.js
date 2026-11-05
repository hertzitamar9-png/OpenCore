import { installGroupWorkspaceUI } from "./group-workspace-ui.js";
import { prepareGroupModel } from "./group-model-preparation.js";

export function invokeGroupNative(command, args = {}) {
  if (command === "group_prepare_model") {
    if (!globalThis.__opencoreGroupPreparation) return Promise.reject(Error("This build has no Chat Core model preparation service."));
    return globalThis.__opencoreGroupPreparation(args.config, args.progress, args.cancelled);
  }
  const native = globalThis.__TAURI_INTERNALS__;
  if (!native?.invoke) return Promise.reject(Error("Open the desktop app to access this PC's models."));
  const run = () => native.invoke(command, args);
  const bridge = globalThis.__opencoreInstallBridge;
  return bridge ? bridge.invoke(command, args, run) : run();
}

export function groupReadyModels(library, providers = [], { includeInstallable = false } = {}) {
  const localProfiles = new Set((library.models || []).flatMap(model => [model.operatingProfileId, model.hubEntry?.profileId]).filter(Boolean));
  const models = [];
  for (const model of library.models || []) {
    if (model.supportsTextChat === false || !(model.supportsTextGeneration || ["text", "brain"].includes(model.category) || model.hubEntry?.category === "text")) continue;
    const ready = !!(model.installed && model.selectable && model.runtimeReady !== false);
    const installable = model.installable !== false && (model.hubEntry ? !!model.hubEntry.runtime?.adapter :
      model.backend === "gguf" || !!model.preparedRuntime || model.selectable && model.runtimeReady !== false);
    if (!ready && !(includeInstallable && installable)) continue;
    const label = (model.label || model.id) + (model.precision && !(model.label || "").includes(model.precision) ? " · " + model.precision : "");
    models.push({ ...model, label, id: model.operatingProfileId || model.hubEntry?.profileId || model.id,
      catalogId: model.id, ready, installable, executionKind: "local" });
  }
  for (const provider of Array.isArray(providers) ? providers : []) {
    const choices = Array.isArray(provider.models) ? provider.models.filter(model => model.enabled && model.available) :
      provider.model && provider.enabled !== false ? [{ id: provider.model, profileId: "api:" + provider.id, contextTokens: provider.contextTokens }] : [];
    for (const model of choices) if (!localProfiles.has(model.profileId) && /^api:[A-Za-z0-9_-]{1,80}(?::(?:[a-f0-9]{2}){1,256})?$/.test(model.profileId))
      models.push({ id: model.profileId, label: provider.name + " · " + model.id, category: "text", installed: true,
        selectable: true, ready: true, catalogId: model.profileId, contextTokens: model.contextTokens, executionKind: "api" });
  }
  const unique = new Map(); for (const model of models) if (!unique.has(model.id)) unique.set(model.id, model);
  return [...unique.values()];
}

export function resolveGroupTargets(text, agents, selected = []) {
  const ids = new Set(selected.filter(id => agents.some(agent => agent.id === id)));
  const names = new Map();
  for (const agent of agents) for (const name of new Set([agent.name, ...(agent.aliases || [])])) {
    const escaped = [...name].map(char => ".*+?^${}()|[]\\".includes(char) ? "\\" + char : char).join("");
    if (!new RegExp("(^|\\s)@" + escaped + "(?=\\s|$|[.,!?])", "i").test(text)) continue;
    const key = name.toLocaleLowerCase();
    names.set(key, [...(names.get(key) || []), agent]);
  }
  for (const matches of names.values()) {
    const explicit = matches.filter(agent => ids.has(agent.id));
    if (matches.length > 1 && !explicit.length) return { agentIds: [], error: "Choose which PC should run @" + matches[0].name };
    for (const agent of matches.length > 1 ? explicit : matches) ids.add(agent.id);
  }
  return { agentIds: [...ids], error: "" };
}

// One worker belongs to the local desktop, independently of the open panel.
// Remote task data never supplies this PC's approval mode or native commands.
export function createGroupWorker({ invoke, service, listen, storage = globalThis.localStorage,
  schedule = setTimeout, cancel = clearTimeout }) {
  const listeners = new Set(), approvals = new Map();
  let state = { task: null, error: "", approvals: [] }, active, polling = false, stopped = false;
  let timer, progressTimer, unlisten;
  const emit = patch => {
    state = { ...state, ...patch, approvals: [...approvals.values()] };
    for (const callback of listeners) callback(state);
  };
  const storageKey = "opencore.group-worker.completed.v1";
  let history;
  try { history = JSON.parse(storage?.getItem(storageKey) || "{}"); } catch { history = {}; }
  if (!history || typeof history !== "object" || Array.isArray(history)) history = {};
  const remember = (key, value) => {
    history[key] = value;
    const keys = Object.keys(history);
    while (keys.length > 100) delete history[keys.shift()];
    // A receipt must be durable before invoking a task; storage failures are
    // surfaced instead of silently allowing duplicate execution after restart.
    if (!storage?.setItem) throw Error("Local task receipts are unavailable. Group execution cannot start safely.");
    storage.setItem(storageKey, JSON.stringify(history));
  };
  const nativeBusy = snapshot => (snapshot.activeConversationIds || []).length > 0 ||
    ["starting", "stopping", "loading"].includes(snapshot.runtime?.status);
  const scopedJobs = (jobs, conversationId) => jobs.filter(job =>
    (job.request?.conversationId || job.conversationId) === conversationId && ["queued", "preparing", "running", "cancelling"].includes(job.status));
  const cancelLocal = async conversationId => {
    if (!conversationId) return;
    await invoke("cancel_chat_message", { conversationId }).catch(() => {});
    const jobs = await invoke("list_studio_jobs", {}).catch(() => []);
    await Promise.allSettled(scopedJobs(jobs, conversationId).map(job => invoke("cancel_studio_job", { id: job.id })));
  };
  const entriesFor = (entries, conversationId) => {
    const result = [], encoder = new TextEncoder();
    for (const entry of (Array.isArray(entries) ? entries : []).filter(value => value.conversationId === conversationId && value.role !== "user").slice(-80).reverse()) {
      const content = String(entry.content || "");
      const preview = { id: entry.id, conversationId, kind: String(entry.kind || "").slice(0, 50), role: entry.role,
        title: String(entry.title || "").slice(0, 200), content: content.length > 4000 ? content.slice(0, 4000) + "\n[Activity preview]" : content, timestamp: entry.timestamp };
      if (encoder.encode(JSON.stringify({ entries: [preview, ...result] })).length <= 24000) result.unshift(preview);
    }
    return result;
  };
  const finalText = entries => entries.filter(entry => entry.kind === "message" && entry.role === "assistant").at(-1)?.content || "";
  const localPreview = (text, conversationId) => text.length <= 32000 ? text : text.slice(0, 30000) +
    "\n\n[Preview only: the complete result is saved on the agent owner's PC.]\n" +
    "opencore-group-local://conversation/" + encodeURIComponent(conversationId);
  const leaseUnavailable = error => /403|revoked|invalid.*lease|membership|task has ended|no longer shared|stopped sharing|lease.*unavailable/i.test(String(error));
  async function deliver(key) {
    const receipt = history[key];
    if (!receipt?.pending) return;
    try {
      await service("worker/update", receipt.pending);
      remember(key, { ...receipt, pending: null });
      if(state.error.startsWith('A group task result is waiting for delivery:'))emit({error:''});
    } catch (error) {
      if (leaseUnavailable(error)) remember(key, { ...receipt, pending: null });
      emit({ error: (leaseUnavailable(error) ? "A group task's delivery lease is no longer available: " : "A group task result is waiting for delivery: ") + String(error) });
    }
  }
  async function terminal(key, base, body, conversationId) {
    remember(key, { status: body.status, text: body.text, error: body.error, conversationId, pending: { ...base, ...body } });
    await deliver(key);
  }
  async function run(job, snapshot) {
    const { connectionId, task } = job, key = connectionId + ":" + task.id;
    const base = { connectionId, taskId: task.id, leaseToken: task.leaseToken };
    const saved = history[key];
    const update = body => service("worker/update", { ...base, ...body });
    if (saved) {
      await terminal(key, base, saved.status === "completed" ? { status: "completed", text: saved.text, progress: { entries: [] } } :
        { status: "failed", error: saved.error || "This task was interrupted on this PC. Create a new task to retry it safely.", progress: { entries: [] } }, saved.conversationId || task.conversationId);
      return;
    }
    let submitted = false, finished = false, leaseLost = false, conversationId = task.conversationId, executionProfile = task.modelId, lastEntries = [];
    try {
      const view = await service("view", { id: connectionId }), sharing = view.sharing;
      const owned = sharing?.models?.find(model => model.enabled !== false && model.id === task.modelId && (!task.agentKey || model.key === task.agentKey));
      if (!sharing?.enabled || !owned)
        throw Error("This model is no longer shared by this PC.");
      const [library, providers] = await Promise.all([invoke("list_model_library", {}), invoke("api_provider_list", {}).catch(() => [])]);
      if (!groupReadyModels(library, providers).some(model => model.id === task.modelId)) {
        const candidate = groupReadyModels(library, providers, { includeInstallable: true }).find(model => model.id === task.modelId);
        if (!candidate || owned.prepareIfMissing !== true || candidate.catalogId !== (owned.catalogId || candidate.catalogId))
          throw Error("The shared local or API model is not ready. Its owner can authorize preparation in Chat Core.");
        if (task.projectId && !(owned.projectBindings?.[task.projectId] || sharing.projectBindings?.[task.projectId]))
          throw Error("Map this shared project to a local project before preparing its model.");
        remember(key, { status: "preparing" });
        active = { ...task, connectionId, conversationId: null, stage: "preparing-model" }; emit({ task: active, error: "" });
        const prepared = await invoke("group_prepare_model", {
          config: { id: task.modelId, catalogId: candidate.catalogId, prepareIfMissing: true },
          progress: async preparation => {
            await service("worker/status", base);
            await update({ status: "running", progress: { entries: [], preparation } });
            emit({ task: { ...active, preparation } });
          },
          cancelled: async () => {
            if (stopped) return true;
            try { await service("worker/status", base); return false; }
            catch (error) { if (leaseUnavailable(error)) return true; throw error; }
          },
        });
        if (!prepared?.profileId) throw Error("The prepared model has no verified execution profile.");
        executionProfile = prepared.profileId;
      }
      let approvalMode = ["ask-every-time", "allow-chat", "allow-all"].includes(sharing.approvalMode)
        ? sharing.approvalMode : "ask-every-time";
      let maxSubagents = Math.max(1, Math.min(16, Math.floor(Number(sharing.maxSubagents) || 3)));
      if (!task.conversationId || !task.leaseToken) throw Error("The group task has no valid local execution lease.");
      remember(key, { status: "allocating" });
      const created = await invoke("create_conversation", { profile: executionProfile });
      if (!created?.id || typeof created.id !== "string") throw Error("Could not create a dedicated local agent chat.");
      conversationId = created.id;
      remember(key, { status: "created", conversationId });
      await service("worker/register", { ...base, conversationId, createdConversation: true });
      active = { ...task, conversationId, connectionId };
      remember(key, { status: "registered", conversationId });
      emit({ task: active, error: "" });
      const progress = async () => {
        const raw = await invoke("get_conversation", { id: conversationId });
        const entries = entriesFor(raw, conversationId);
        lastEntries = entries;
        if (finished) return entries;
        await service("worker/status", base);
        await update({ status: "running", progress: { entries } });
        return entries;
      };
      await progress();
      const heartbeat = async () => {
        try { await progress(); }
        catch (error) {
          if (finished) return;
          emit({ error: String(error) });
          if (leaseUnavailable(error)) {
            leaseLost = true;
            if (submitted) await cancelLocal(conversationId);
          }
        }
        if (!finished && !stopped && !leaseLost) progressTimer = schedule(heartbeat, 1500);
      };
      progressTimer = schedule(heartbeat, 1500);
      // Recheck immediately before switching a model. A task stays leased to
      // this worker while a foreground conversation wins the idle race.
      for (;;) {
        const current = await invoke("get_snapshot", {});
        if (!nativeBusy(current)) { snapshot = current; break; }
        await new Promise(resolve => schedule(resolve, 1000));
        if (stopped) throw Error("The local group worker stopped before execution.");
      }
      if (snapshot.runtime?.profile !== executionProfile || snapshot.runtime?.status !== "running")
        await invoke("start_profile", { request: { profile: executionProfile, attachUrl: null } });
      if (leaseLost || stopped) throw Error("Sharing stopped before the group task could execute.");
      const latest = (await service("view", { id: connectionId })).sharing;
      if (!latest?.enabled || !latest.models?.some(model => model.id === task.modelId)) throw Error("This PC stopped sharing this model.");
      approvalMode = ["ask-every-time", "allow-chat", "allow-all"].includes(latest.approvalMode) ? latest.approvalMode : "ask-every-time";
      maxSubagents = Math.max(1, Math.min(16, Math.floor(Number(latest.maxSubagents) || 3)));
      const config = latest.models.find(model => model.enabled !== false && model.id === task.modelId && (!task.agentKey || model.key === task.agentKey));
      if (!config) throw Error("The shared agent's configuration changed before execution.");
      if (["ask-every-time", "allow-chat", "allow-all"].includes(config.approvalMode)) approvalMode = config.approvalMode;
      // Group members may request stricter approval, never bypass this PC's policy.
      const approvalRank = ["ask-every-time", "allow-chat", "allow-all"];
      const requestedApproval = task.requestSettings?.approval;
      if (approvalRank.includes(requestedApproval)) approvalMode = approvalRank[Math.min(approvalRank.indexOf(approvalMode), approvalRank.indexOf(requestedApproval))];
      const requestedEffort = task.requestSettings?.effort;
      const reasoningEffort = ["off", "low", "medium", "high", "ultra"].includes(requestedEffort) ? requestedEffort : config.reasoningEffort || "medium";
      maxSubagents = Math.min(maxSubagents, Math.max(1, Math.min(8, Math.floor(Number(config.maxSubagents) || 3))));
      const projectId = config.projectBindings?.[task.projectId] || latest.projectBindings?.[task.projectId];
      if (task.projectId && !projectId) throw Error("Map this shared project to a local project in Share my PC before running its agents.");
      if (projectId) {
        const current = await invoke("get_snapshot", {});
        if (!current.projects?.some(project => project.id === projectId && project.folderAvailable !== false)) throw Error("The owner's mapped local project is unavailable.");
        await invoke("move_conversation_to_project", { id: conversationId, projectId });
      }
      const atSubmit = await invoke("get_snapshot", {});
      if (nativeBusy(atSubmit) || atSubmit.runtime?.profile !== executionProfile || atSubmit.runtime?.status !== "running")
        throw Error("The selected runtime changed or this PC became busy before submission. No task was sent.");
      await service("worker/status", base);
      if (leaseLost || stopped) throw Error("Sharing stopped before submission. No task was sent.");
      remember(key, { status: "submitted", conversationId });
      submitted = true;
      await invoke("send_chat_message", { request: {
        conversationId, text: [config.instructions ? "PC owner's agent instructions:\n" + config.instructions : "", task.context, task.text].filter(Boolean).join("\n"),
        files: [], reasoningEffort, approvalMode, skills: config.skills || [], subagentsEnabled: config.subagentsEnabled !== false,
        maxSubagents, projectSkillsEnabled: true, compactAtTokens: 27852, submissionId: task.id,
      } });
      for (;;) {
        if (leaseLost || stopped) throw Error("Sharing stopped while this group task was running.");
        const [current, jobs] = await Promise.all([invoke("get_snapshot", {}), invoke("list_studio_jobs", {})]);
        const busy = current.activeConversationIds?.includes(conversationId) || jobs.some(job =>
          (job.request?.conversationId || job.conversationId) === conversationId && ["queued", "preparing", "running", "cancelling"].includes(job.status));
        if (!busy) break;
        await new Promise(resolve => schedule(resolve, 1000));
      }
      finished = true; cancel(progressTimer);
      const raw = await invoke("get_conversation", { id: conversationId });
      const entries = entriesFor(raw, conversationId);
      lastEntries = entries;
      const text = localPreview(finalText(raw.filter(entry => entry.conversationId === conversationId)), conversationId);
      const errorIndex = raw.findLastIndex(entry => entry.conversationId === conversationId && entry.kind === "error");
      const finalIndex = raw.findLastIndex(entry => entry.conversationId === conversationId && entry.kind === "message" && entry.role === "assistant");
      const error = errorIndex > finalIndex && raw[errorIndex];
      if (error) throw Error(error.content || "The shared agent failed. Open its activity for details.");
      await terminal(key, base, { status: "completed", text, progress: { entries } }, conversationId);
    } catch (error) {
      const message = String(error);
      if (history[key]?.status === "completed") {
        emit({ error: "The task finished, but its result could not be delivered: " + message });
        return;
      }
      emit({ error: message });
      await terminal(key, base, { status: "failed", error: message, progress: { entries: lastEntries } }, conversationId).catch(() => {});
    } finally {
      finished = true; cancel(progressTimer); active = null;
      approvals.clear(); emit({ task: null });
    }
  }
  async function poll() {
    if (polling || stopped) return;
    polling = true;
    try {
      for (const key of Object.keys(history)) if (history[key].pending) await deliver(key);
      const list = await service("list", {});
      if (!list.groups?.length) return;
      const snapshot = await invoke("get_snapshot", {});
      if (nativeBusy(snapshot)) return;
      const studio = await invoke("list_studio_jobs", {});
      if (studio.some(job => ["queued", "preparing", "running", "cancelling"].includes(job.status))) return;
      const claimed = await service("worker/poll", {});
      if (claimed.jobs?.length) await run(claimed.jobs[0], snapshot);
    } catch (error) { emit({ error: String(error) }); }
    finally { polling = false; }
  }
  async function loop() {
    await poll();
    if (!stopped) timer = schedule(loop, 2500);
  }
  return {
    poll, getState: () => state,
    subscribe(callback) { listeners.add(callback); return () => listeners.delete(callback); },
    async start() {
      stopped = false;
      unlisten = await listen?.("opencore-tool-approval-request", event => {
        const request = event.payload;
        if (!active || request.conversationId !== active.conversationId) return;
        approvals.set(request.requestId, { ...request, agentName: active.agentName || "Group agent" }); emit({});
      });
      timer = schedule(loop, 0);
    },
    async answer(requestId, approved) {
      const request = approvals.get(requestId);
      if (!request || request.conversationId !== active?.conversationId) return false;
      await invoke("resolve_tool_approval", { requestId, approved });
      approvals.delete(requestId); emit({}); return true;
    },
    async readResult(connectionId, conversationId) {
      if (!Object.entries(history).some(([key, receipt]) => key.startsWith(connectionId + ":") && receipt.conversationId === conversationId && receipt.status === "completed"))
        throw Error("The complete result is available only on the PC that ran this group task.");
      const entries = await invoke("get_conversation", { id: conversationId });
      return finalText(entries.filter(entry => entry.conversationId === conversationId));
    },
    stop() { stopped = true; cancel(timer); cancel(progressTimer); unlisten?.(); return active ? cancelLocal(active.conversationId) : Promise.resolve(); },
  };
}

export function installGroupFeatures(React, listen, preparation) {
  const h = React.createElement;
  const invoke = invokeGroupNative;
  const service = (action, body = {}) => globalThis.__opencoreGroupService(action, body);
  globalThis.__opencoreGroupPreparation = (config, progress, cancelled) => prepareGroupModel(config, preparation, progress, cancelled);
  let worker = globalThis.__opencoreGroupWorker;
  if (!worker && !globalThis.__OPENCORE_MOBILE__ && globalThis.__TAURI_INTERNALS__?.invoke) {
    worker = createGroupWorker({ invoke, service, listen });
    globalThis.__opencoreGroupWorker = worker;
    worker.start().catch(() => {});
    window.addEventListener("beforeunload", () => worker.stop(), { once: true });
  }
  const button = (text, onClick, props = {}) => h("button", { type: "button", onClick, ...props }, text);
  const field = (label, control) => h("label", { className: "group-field" }, h("span", null, label), control);
  const statusLabel = status => ({ queued: "Queued", running: "Working", completed: "Completed", failed: "Failed", cancelled: "Cancelled", pending: "Pending", done: "Done" }[status] || status);
  function useWorker() {
    const [state, setState] = React.useState(() => worker?.getState() || { approvals: [], task: null, error: "" });
    React.useEffect(() => worker?.subscribe(setState), []);
    return state;
  }
  function Dialog({ label, className = "", close, children }) {
    const ref = React.useRef(null);
    React.useLayoutEffect(() => {
      const previous = document.activeElement, dialog = ref.current;
      dialog.showModal();
      return () => { dialog.close(); previous?.focus?.({ preventScroll: true }); };
    }, []);
    return h("dialog", { ref, className: "group-dialog " + className, "aria-label": label,
      onCancel: event => { event.preventDefault(); close(); } }, children);
  }
  function Board({ data }) {
    const tasks = data?.tasks || [], plan = data?.plan || [];
    return h("section", { className: "group-board", "aria-label": "Shared task plan" },
      h("h3", null, "Shared plan"),
      !plan.length && !tasks.length ? h("p", { className: "group-muted" }, "Ask a shared agent to start a task. Its plan and progress appear here.") : null,
      plan.map(item => h("div", { className: "group-plan-item", key: item.taskId + ":" + item.id },
        h("span", { className: "group-status " + item.status }, statusLabel(item.status)), h("p", null, item.text), h("small", null, item.agentName))),
      tasks.map(task => h("article", { className: "group-task", key: task.id },
        h("div", { className: "group-task-heading" }, h("strong", null, task.agentName || "Agent"), h("span", { className: "group-status " + task.status }, statusLabel(task.status))),
        h("p", null, task.text), task.error ? h("p", { className: "group-error" }, task.error) : null,
        task.progress?.preparation ? h("div", { className: "group-model-preparation", role: "status" },
          h("strong", null, "Preparing local model"), h("p", null, task.progress.preparation.currentFile || task.progress.preparation.phase),
          h("progress", { max: task.progress.preparation.totalBytes || undefined, value: task.progress.preparation.totalBytes ? task.progress.preparation.downloadedBytes || 0 : undefined })) : null,
        task.progress?.entries?.length ? h("details", null, h("summary", null, "Task activity"),
          task.progress.entries.map((entry, index) => h("p", { key: entry.id || index }, h("strong", null, entry.title || entry.kind || "Activity"), " ", entry.content))) : null)));
  }
  function EmbeddedWorkspace({ label, className = "", children }) {
    return h("div", { className: "group-dialog group-embedded " + className, role: "region", "aria-label": label }, children);
  }
  const GroupPanel = installGroupWorkspaceUI(React, {service,invoke,worker,Dialog,EmbeddedWorkspace,Board,groupReadyModels,resolveGroupTargets,SpeechButton: preparation?.speechButton});
  function Approval({ request }) {
    const [busy, setBusy] = React.useState(false), [error, setError] = React.useState("");
    const answer = async approved => {
      if (busy) return; setBusy(true);
      try { await worker.answer(request.requestId, approved); } catch (failure) { setError(String(failure)); setBusy(false); }
    };
    return h(Dialog, { label: "Approve group agent tool", className: "group-approval", close: () => answer(false) },
      h("h2", null, "Approve this action on your PC?"), h("p", null, request.agentName + " wants to use " + request.name + "."),
      h("pre", null, typeof request.arguments === "string" ? request.arguments : JSON.stringify(request.arguments, null, 2)),
      error ? h("p", { className: "group-error", role: "alert" }, error) : null,
      h("div", { className: "group-header-actions" }, button("Deny", () => answer(false), { disabled: busy }), button("Approve", () => answer(true), { disabled: busy })));
  }
  function GroupIcon() {
    return h("svg", { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true },
      h("circle", { cx: 12, cy: 7, r: 2.7 }),
      h("circle", { cx: 4.8, cy: 9, r: 1.8 }),
      h("circle", { cx: 19.2, cy: 9, r: 1.8 }),
      h("path", { d: "M7.3 20v-1.1c0-2.5 2.1-4.5 4.7-4.5s4.7 2 4.7 4.5V20" }),
      h("path", { d: "M1.5 19.5v-.7c0-1.8 1.5-3.2 3.3-3.2.6 0 1.2.2 1.7.4M22.5 19.5v-.7c0-1.8-1.5-3.2-3.3-3.2-.6 0-1.2.2-1.7.4" }));
  }
  function GroupEntry({ navigation = false, overview = false, selected = false } = {}) {
    const state = useWorker();
    const caption = "Chat Core" + (state.approvals.length ? " · " + state.approvals.length : "");
    const content = overview
      ? h(React.Fragment, null, h(GroupIcon), h("span", null, h("strong", null, caption), h("small", null, "Group chats with people and agents")))
      : navigation ? h(React.Fragment, null, h(GroupIcon), h("span", null, caption)) : caption;
    return button(content,
      () => { document.body.classList.remove("oc-chats-open", "oc-more-open"); window.dispatchEvent(new Event("opencore-open-groups")); },
      { className: overview ? "overview-group-entry" : navigation ? "nav-item group-nav-entry" + (selected ? " active" : "") : "new-chat-button group-entry", "aria-label": overview ? "Open Chat Core group chats" : navigation ? "Open Chat Core" : "Chat Core", "aria-current": selected ? "page" : undefined, title: "Chat Core" });
  }
  function GroupPage() {
    const state = useWorker();
    return h(GroupPanel, { embedded: true, workerState: state });
  }
  function GroupHost() {
    const state = useWorker();
    return h(React.Fragment, null,
      state.approvals[0] ? h(Approval, { request: state.approvals[0], key: state.approvals[0].requestId }) : null);
  }
  globalThis.__opencoreGroupEntry = GroupEntry;
  globalThis.__opencoreGroupPage = GroupPage;
  globalThis.__opencoreGroupHost = GroupHost;
}
