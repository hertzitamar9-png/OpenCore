export function invokeGroupNative(command, args = {}) {
  const native = globalThis.__TAURI_INTERNALS__;
  if (!native?.invoke) return Promise.reject(Error("Open the desktop app to access this PC's models."));
  const run = () => native.invoke(command, args);
  const bridge = globalThis.__opencoreInstallBridge;
  return bridge ? bridge.invoke(command, args, run) : run();
}

export function groupReadyModels(library) {
  return [...new Map((library.models || []).filter(model => model.installed && model.selectable && (model.category === "text" || model.hubEntry?.category === "text"))
    .map(model => { const profile = { ...model, id: model.operatingProfileId || model.hubEntry?.profileId || model.id }; return [profile.id, profile]; })).values()];
}

export function resolveGroupTargets(text, agents, selected = []) {
  const ids = new Set(selected.filter(id => agents.some(agent => agent.id === id)));
  const names = new Map();
  for (const agent of agents) {
    const escaped = agent.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!new RegExp("(^|\\s)@" + escaped + "(?=\\s|$|[.,!?])", "i").test(text)) continue;
    const key = agent.name.toLocaleLowerCase();
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
  const entriesFor = (entries, conversationId) => {
    const result = [], encoder = new TextEncoder();
    for (const entry of (Array.isArray(entries) ? entries : []).filter(value => value.conversationId === conversationId).slice(-80).reverse()) {
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
    let submitted = false, finished = false, leaseLost = false;
    try {
      const view = await service("view", { id: connectionId }), sharing = view.sharing;
      if (!sharing?.enabled || !sharing.models?.some(model => model.id === task.modelId))
        throw Error("This model is no longer shared by this PC.");
      const library = await invoke("list_model_library", {});
      if (!groupReadyModels(library).some(model => model.id === task.modelId))
        throw Error("The shared model is not installed or ready on this PC.");
      let approvalMode = ["ask-every-time", "allow-chat", "allow-all"].includes(sharing.approvalMode)
        ? sharing.approvalMode : "ask-every-time";
      let maxSubagents = Math.max(1, Math.min(16, Math.floor(Number(sharing.maxSubagents) || 3)));
      if (!task.conversationId || !task.leaseToken) throw Error("The group task has no valid local execution lease.");
      await service("worker/register", { ...base, conversationId: task.conversationId });
      active = { ...task, connectionId };
      remember(key, { status: "registered", conversationId: task.conversationId });
      emit({ task: active, error: "" });
      const progress = async () => {
        const raw = await invoke("get_conversation", { id: task.conversationId });
        const entries = entriesFor(raw, task.conversationId);
        if (finished) return entries;
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
            if (submitted) await invoke("cancel_chat_message", { conversationId: task.conversationId }).catch(() => {});
          }
        }
        if (!finished && !stopped && !leaseLost) progressTimer = schedule(heartbeat, 1500);
      };
      progressTimer = schedule(heartbeat, 1500);
      // Recheck immediately before switching a model. A task stays leased to
      // this worker while a foreground conversation wins the idle race.
      for (;;) {
        const current = await invoke("get_snapshot", {});
        if (!nativeBusy(current)) break;
        await new Promise(resolve => schedule(resolve, 1000));
        if (stopped) throw Error("The local group worker stopped before execution.");
      }
      if (snapshot.runtime?.profile !== task.modelId || snapshot.runtime?.status !== "running")
        await invoke("start_profile", { request: { profile: task.modelId, attachUrl: null } });
      if (leaseLost || stopped) throw Error("Sharing stopped before the group task could execute.");
      const latest = (await service("view", { id: connectionId })).sharing;
      if (!latest?.enabled || !latest.models?.some(model => model.id === task.modelId)) throw Error("This PC stopped sharing this model.");
      approvalMode = ["ask-every-time", "allow-chat", "allow-all"].includes(latest.approvalMode) ? latest.approvalMode : "ask-every-time";
      maxSubagents = Math.max(1, Math.min(16, Math.floor(Number(latest.maxSubagents) || 3)));
      remember(key, { status: "submitted", conversationId: task.conversationId });
      submitted = true;
      await invoke("send_chat_message", { request: {
        conversationId: task.conversationId, text: [task.context, task.text].filter(Boolean).join("\n"),
        files: [], reasoningEffort: "medium", approvalMode, skills: [], subagentsEnabled: true,
        maxSubagents, projectSkillsEnabled: true, compactAtTokens: 27852, submissionId: task.id,
      } });
      for (;;) {
        if (leaseLost || stopped) throw Error("Sharing stopped while this group task was running.");
        const [current, jobs] = await Promise.all([invoke("get_snapshot", {}), invoke("list_studio_jobs", {})]);
        const busy = current.activeConversationIds?.includes(task.conversationId) || jobs.some(job =>
          (job.request?.conversationId || job.conversationId) === task.conversationId && ["queued", "preparing", "running", "cancelling"].includes(job.status));
        if (!busy) break;
        await new Promise(resolve => schedule(resolve, 1000));
      }
      finished = true; cancel(progressTimer);
      const raw = await invoke("get_conversation", { id: task.conversationId });
      const entries = entriesFor(raw, task.conversationId);
      const text = localPreview(finalText(raw.filter(entry => entry.conversationId === task.conversationId)), task.conversationId);
      await terminal(key, base, { status: "completed", text, progress: { entries } }, task.conversationId);
    } catch (error) {
      const message = String(error);
      if (history[key]?.status === "completed") {
        emit({ error: "The task finished, but its result could not be delivered: " + message });
        return;
      }
      emit({ error: message });
      await terminal(key, base, { status: "failed", error: message, progress: { entries: [] } }, task.conversationId).catch(() => {});
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
    stop() { stopped = true; cancel(timer); cancel(progressTimer); unlisten?.(); },
  };
}

export function installGroupFeatures(React, listen) {
  const h = React.createElement;
  const invoke = invokeGroupNative;
  const service = (action, body = {}) => globalThis.__opencoreGroupService(action, body);
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
        task.progress?.entries?.length ? h("details", null, h("summary", null, "Task activity"),
          task.progress.entries.map((entry, index) => h("p", { key: entry.id || index }, h("strong", null, entry.title || entry.kind || "Activity"), " ", entry.content))) : null)));
  }
  function GroupPanel({ close, workerState }) {
    const [groups, setGroups] = React.useState([]), [id, setId] = React.useState("");
    const [data, setData] = React.useState(null), [tab, setTab] = React.useState("Groups");
    const [mode, setMode] = React.useState(""), [name, setName] = React.useState("");
    const [displayName, setDisplayName] = React.useState(""), [joinCode, setJoinCode] = React.useState("");
    const [invite, setInvite] = React.useState(null), [notice, setNotice] = React.useState("");
    const [localResult, setLocalResult] = React.useState(null);
    const [error, setError] = React.useState(""), [pending, setPending] = React.useState(false);
    const [models, setModels] = React.useState([]), [modelError, setModelError] = React.useState("");
    const [sharing, setSharing] = React.useState(null), [dirty, setDirty] = React.useState(false);
    const [drafts, setDrafts] = React.useState({}), [targets, setTargets] = React.useState({});
    const [compact, setCompact] = React.useState(innerWidth < 780);
    const request = React.useRef(null), messages = React.useRef(null), previousMessageCount = React.useRef(0);
    const currentId = React.useRef(id), mutationEpoch = React.useRef(0); currentId.current = id;
    React.useEffect(() => {
      const resize = () => setCompact(innerWidth < 780);
      window.addEventListener("resize", resize); return () => window.removeEventListener("resize", resize);
    }, []);
    React.useEffect(() => {
      let alive = true, timer;
      const refresh = async () => {
        try {
          const result = await service("list");
          if (!alive) return;
          setGroups(result.groups || []); setDisplayName(current => current || result.localName || "");
          if (id) {
            const epoch = mutationEpoch.current;
            const view = await service("view", { id });
            if (alive && currentId.current === id && epoch === mutationEpoch.current) setData(view);
          }
        } catch (failure) { if (alive) setError(String(failure)); }
        if (alive) timer = setTimeout(refresh, 2000);
      };
      refresh(); return () => { alive = false; clearTimeout(timer); };
    }, [id]);
    React.useEffect(() => {
      let alive = true;
      invoke("list_model_library").then(library => {
        if (alive) setModels(groupReadyModels(library));
      }).catch(failure => { if (alive) setModelError(String(failure)); });
      return () => { alive = false; };
    }, []);
    React.useEffect(() => {
      if (data?.sharing && !dirty) setSharing({ enabled: false, models: [], approvalMode: "ask-every-time", maxSubagents: 3, ...data.sharing });
    }, [data, dirty]);
    React.useEffect(() => {
      const list = messages.current, count = data?.messages?.length || 0;
      if (list && count !== previousMessageCount.current && (list.scrollHeight - list.scrollTop - list.clientHeight < 180 || !previousMessageCount.current))
        list.scrollTop = list.scrollHeight;
      previousMessageCount.current = count;
    }, [data?.messages?.length, tab]);
    const select = groupId => {
      setData(null); setSharing(null); setDirty(false); setInvite(null); setError(""); setNotice("");
      setId(groupId); setTab("Chat"); setMode(""); request.current = null; previousMessageCount.current = 0;
    };
    const act = async (action, body, accept) => {
      if (pending) return;
      mutationEpoch.current++;
      setPending(true); setError(""); setNotice("");
      try { const result = await service(action, body); accept?.(result); }
      catch (failure) { setError(String(failure)); }
      finally { mutationEpoch.current++; setPending(false); }
    };
    const createOrJoin = event => {
      event.preventDefault();
      const action = mode === "create" ? "create" : "join";
      act(action, action === "create" ? { name: name.trim(), displayName: displayName.trim() } : { invite: joinCode.trim(), displayName: displayName.trim() }, result => {
        setGroups(current => [...current.filter(group => group.id !== result.id), result]); select(result.id); setJoinCode(""); setName("");
      });
    };
    const groupSidebar = h("aside", { className: "group-sidebar", "aria-label": "Your groups" },
      h("h2", null, "Your groups"), h("p", { className: "group-muted" }, "Private chats with friends and their shared models."),
      h("div", { className: "group-create-actions" }, button("Create group", () => setMode(mode === "create" ? "" : "create")), button("Join with invite", () => setMode(mode === "join" ? "" : "join"))),
      mode ? h("form", { className: "group-join-form", onSubmit: createOrJoin },
        field("Your name", h("input", { "aria-label": "Your name", value: displayName, maxLength: 64, onChange: event => setDisplayName(event.target.value), required: true })),
        mode === "create" ? field("Group name", h("input", { "aria-label": "Group name", value: name, maxLength: 100, onChange: event => setName(event.target.value), required: true }))
          : field("Paste group invite", h("textarea", { "aria-label": "Paste group invite", value: joinCode, onChange: event => setJoinCode(event.target.value), spellCheck: false, required: true, rows: 3 })),
        button(mode === "create" ? "Create group" : "Join group", null, { type: "submit", disabled: pending || !displayName.trim() || !(mode === "create" ? name.trim() : joinCode.trim()) }),
        button("Cancel", () => setMode(""))) : null,
      groups.length ? h("nav", { className: "group-list", "aria-label": "Groups" }, groups.map(group => button(
        h(React.Fragment, null, h("strong", null, group.name), h("small", null, group.error || (group.hosting ? "Hosted on this PC" : group.connected ? "Connected" : "Reconnecting…"))),
        () => select(group.id), { key: group.id, disabled: pending, className: group.id === id ? "active" : "", "aria-current": group.id === id ? "page" : undefined })))
        : h("p", { className: "group-empty" }, "Create a group or paste an invite to get started."));
    const agents = data?.agents || [], draft = drafts[id] || "", selectedTargets = targets[id] || [];
    const agentNames = agents.filter(agent => selectedTargets.includes(agent.id)).map(agent => agent.name);
    const send = event => {
      event?.preventDefault(); if (!draft.trim() || !data) return;
      const resolved = resolveGroupTargets(draft, agents, selectedTargets);
      if (resolved.error) { setError(resolved.error); return; }
      const agentIds = resolved.agentIds;
      const signature = JSON.stringify([id, draft, agentIds]);
      if (request.current?.signature !== signature) request.current = { signature, requestId: crypto.randomUUID() };
      act("message", { id, text: draft, requestId: request.current.requestId, agentIds }, result => {
        setData(result); setDrafts(current => ({ ...current, [id]: "" })); request.current = null;
      });
    };
    const askAgent = agent => {
      setDrafts(current => ({ ...current, [id]: (current[id] || "") + (current[id] ? " " : "") + "@" + agent.name + " " }));
      setTargets(current => ({ ...current, [id]: [...new Set([...(current[id] || []), agent.id])] })); setTab("Chat");
    };
    const agentList = h("section", { className: "group-people", "aria-label": "Members and agents" },
      h("h3", null, "Shared agents"), agents.length ? agents.map(agent => h("article", { key: agent.id, className: "group-agent" },
        h("div", null, h("strong", null, "@" + agent.name), h("small", null, agent.modelLabel || agent.modelId), h("small", null, agent.pcName + (agent.online ? " · Online" : " · Offline"))),
        button("Ask agent", () => askAgent(agent), { disabled: !agent.online, "aria-label": "Ask " + agent.name + " on " + agent.pcName })))
        : h("p", { className: "group-muted" }, "No models are shared yet. Each PC owner chooses what to share."),
      h("h3", null, "Members"), (data?.members || []).map(member => h("article", { key: member.id, className: "group-member" },
        h("div", null, h("strong", null, member.name), h("small", null, member.pcName + (member.online ? " · Online" : " · Offline"))),
        data.self?.role === "owner" && member.id !== data.self.id ? button("Remove", () => {
          if (confirm("Remove " + member.name + " from this group?")) act("revoke", { id, memberId: member.id }, setData);
        }, { "aria-label": "Remove " + member.name + " from group" }) : null)));
    const chat = h("div", { className: "group-chat-layout" }, h("section", { className: "group-chat", "aria-label": "Group conversation" },
      h("div", { className: "group-messages", ref: messages, role: "log", "aria-label": "Group messages", "aria-live": "polite" },
        data?.messages?.length ? data.messages.map(message => h("article", { key: message.id, className: "group-message " + message.kind },
          h("header", null, h("strong", null, message.authorName || "Group"), message.kind === "agent" ? h("span", { className: "group-agent-badge" }, "Agent") : null,
            h("time", null, new Date(message.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))), h("div", { className: "group-message-text" }, message.text),
          /opencore-group-local:\/\/conversation\/([^\s]+)/.test(message.text) ? button("Open complete result on this PC", async () => {
            const match = message.text.match(/opencore-group-local:\/\/conversation\/([^\s]+)/);
            try { setLocalResult(await worker?.readResult(id, decodeURIComponent(match[1])) || "The full result is on the agent owner's PC."); }
            catch (failure) { setError(String(failure)); }
          }, { className: "group-result-link" }) : null))
          : h("div", { className: "group-welcome" }, h("h3", null, "Start the conversation"), h("p", null, "Send a message to everyone, or mention a shared @agent to give it a task."))),
      h("form", { className: "group-composer", onSubmit: send },
        agents.length ? h("details", { className: "group-targets" }, h("summary", null, "Choose agents", agentNames.length ? " · " + agentNames.join(", ") : ""),
          agents.map(agent => h("label", { key: agent.id }, h("input", { type: "checkbox", checked: selectedTargets.includes(agent.id), disabled: !agent.online,
            "aria-label": "Ask " + agent.name + " on " + agent.pcName,
            onChange: event => setTargets(current => ({ ...current, [id]: event.target.checked ? [...new Set([...selectedTargets, agent.id])] : selectedTargets.filter(value => value !== agent.id) })) }),
            h("span", null, "@" + agent.name, h("small", null, agent.modelLabel + " · " + agent.pcName))))) : null,
        h("div", { className: "group-compose-row" }, h("textarea", { "aria-label": "Group message", placeholder: "Message the group, or @agent…", value: draft, rows: 2,
          onChange: event => setDrafts(current => ({ ...current, [id]: event.target.value })), onKeyDown: event => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
          } }), button(pending ? "Sending…" : "Send", null, { type: "submit", "aria-label": "Send group message", disabled: pending || !draft.trim() })),
        h("small", { className: "group-muted" }, "Enter to send · Shift+Enter for a new line. Each PC queues work while its models are busy."))),
      h("aside", { className: "group-chat-plan" }, h(Board, { data })));
    const editSharing = patch => { setDirty(true); setSharing(current => ({ ...current, ...patch })); };
    const selectedModels = sharing?.models || [];
    const sharingModels = [...models, ...selectedModels.filter(model => !models.some(installed => installed.id === model.id))];
    const validSharing = !sharing?.enabled || selectedModels.length > 0 && selectedModels.every(model => models.some(installed => installed.id === model.id));
    const sharingPanel = sharing ? h("form", { className: "group-sharing", onSubmit: event => {
      event.preventDefault(); if (validSharing) act("sharing", { id, ...sharing }, result => {
        setDirty(false);
        if (result.localOnly) {
          setSharing(result.sharing); setData(current => current ? { ...current, sharing: result.sharing } : current);
        } else setData(result);
        setNotice(result.notice || "Sharing saved for this PC.");
      });
    } }, h("h3", null, "Your PC, your models"), h("p", null, "Sharing starts off. Friends can ask only the models you choose below. Tool approvals stay with you on this PC."),
      h("label", { className: "group-toggle" }, h("input", { type: "checkbox", "aria-label": "Share models from this PC", checked: !!sharing.enabled, onChange: event => editSharing({ enabled: event.target.checked }) }), "Share models from this PC"),
      modelError ? h("p", { className: "group-error" }, modelError) : null,
      !models.length ? h("p", { className: "group-muted" }, "Install a text model in Models before sharing an agent.") : null,
      sharingModels.map(model => {
        const selected = selectedModels.find(value => value.id === model.id), installed = models.some(value => value.id === model.id);
        return h("div", { className: "group-share-model", key: model.id }, h("label", null,
          h("input", { type: "checkbox", "aria-label": "Share " + model.label, checked: !!selected, disabled: !installed && !selected,
            onChange: event => editSharing({ models: event.target.checked ? [...selectedModels, { id: model.id, label: model.label, name: String(model.label || model.id).slice(0, 48) }] : selectedModels.filter(value => value.id !== model.id) }) }),
          h("span", null, model.label, !installed ? h("small", { className: "group-error" }, "No longer installed on this PC") : null)),
          selected ? field("Agent name", h("input", { "aria-label": "Agent name for " + model.label, value: selected.name || "", maxLength: 48, required: true,
            onChange: event => editSharing({ models: selectedModels.map(value => value.id === model.id ? { ...value, name: event.target.value } : value) }) })) : null);
      }),
      field("Tools requested by group agents", h("select", { "aria-label": "Group tool approval", value: sharing.approvalMode, onChange: event => editSharing({ approvalMode: event.target.value }) },
        h("option", { value: "ask-every-time" }, "Ask me before every tool action"), h("option", { value: "allow-chat" }, "Allow tools for these group tasks"), h("option", { value: "allow-all" }, "Allow all tools for shared agents"))),
      sharing.approvalMode !== "ask-every-time" ? h("p", { className: "group-permission-note" }, "Shared agents may use this PC's enabled tools without asking for each action. Save only if you want to grant that access.") : null,
      field("Maximum local subagents", h("input", { type: "number", min: 1, max: 16, "aria-label": "Maximum local subagents", value: sharing.maxSubagents,
        onChange: event => editSharing({ maxSubagents: Math.max(1, Math.min(16, Number(event.target.value) || 1)) }) })),
      !validSharing ? h("p", { className: "group-error" }, "Choose at least one installed model before enabling sharing.") : null,
      button("Save sharing", null, { type: "submit", disabled: pending || !dirty || !validSharing })) : h("p", null, "Loading local sharing settings…");
    const selectedGroup = groups.find(group => group.id === id);
    return h(Dialog, { label: "Shared groups", close },
      h("header", { className: "group-dialog-header" }, h("div", null, h("h1", null, "Shared groups"), h("p", null, "People and models working together")),
        button("×", close, { "aria-label": "Close shared groups", className: "group-close" })),
      h("div", { className: "group-body" }, !compact ? groupSidebar : null,
        h("main", { className: "group-main" }, h("header", { className: "group-main-header" }, h("div", null,
          h("h2", null, data?.group?.name || selectedGroup?.name || "Your groups"),
          selectedGroup ? h("small", { className: "group-muted" }, selectedGroup.hosting ? "Hosted on this PC" : selectedGroup.connected ? "Connected" : selectedGroup.error || "Reconnecting…") : null),
          id ? h("div", { className: "group-header-actions" }, data?.self?.role === "owner" ? button("Create invite", () => act("invite", { id }, setInvite), { disabled: pending }) : null,
            button("Leave", () => { if (confirm("Leave this group?")) act("leave", { id }, () => { setGroups(current => current.filter(group => group.id !== id)); select(""); setTab("Groups"); }); }, { disabled: pending || data?.self?.role === "owner", title: data?.self?.role === "owner" ? "This PC hosts this group" : undefined })) : null),
          h("nav", { className: "group-tabs", role: "tablist", "aria-label": "Group views" }, ["Groups", "Chat", "Agents", "Plan", "Share my PC"].map(value => button(value, () => setTab(value),
            { key: value, role: "tab", "aria-selected": value === tab, disabled: value !== "Groups" && !id }))),
          error ? h("p", { className: "group-error group-banner", role: "alert" }, error) : null,
          notice ? h("p", { className: "group-banner", role: "status" }, notice) : null,
          workerState.task ? h("p", { className: "group-banner" }, "This PC is working on a shared task. ", workerState.task.agentName || workerState.task.modelId) : null,
          workerState.error ? h("p", { className: "group-error group-banner" }, workerState.error) : null,
          invite ? h("section", { className: "group-invite" }, field("Group invite", h("input", { "aria-label": "Group invite", readOnly: true, value: invite.invite })),
            button("Copy invite", async () => { try { await navigator.clipboard.writeText(invite.invite); setNotice("Invite copied."); } catch { setNotice("Select and copy the invite above."); } }),
            h("small", null, "One-time invite · expires in " + Math.ceil((invite.expiresIn || 300) / 60) + " minutes. Your friend's PC must be able to reach this host.")) : null,
          h("div", { className: "group-content " + (tab === "Chat" ? "group-content-chat" : "") },
            tab === "Groups" ? compact ? groupSidebar : h("div", { className: "group-welcome" }, h("h3", null, "Your shared workspace"), h("p", null, "Choose a group on the left, or invite friends to a new one."))
              : !data ? h("p", { className: "group-empty" }, "Loading group…") : tab === "Chat" ? chat : tab === "Agents" ? agentList : tab === "Plan" ? h(Board, { data }) : sharingPanel))),
      localResult !== null ? h(Dialog, { label: "Complete local group result", className: "group-local-result", close: () => setLocalResult(null) },
        h("header", { className: "group-dialog-header" }, h("h2", null, "Complete result on this PC"), button("Close", () => setLocalResult(null))),
        h("pre", { className: "group-local-result-text" }, localResult)) : null);
  }
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
  function GroupEntry() {
    const state = useWorker();
    return button("Groups" + (state.approvals.length ? " · " + state.approvals.length : ""), () => window.dispatchEvent(new Event("opencore-open-groups")),
      { className: "new-chat-button group-entry", "aria-label": "Groups" });
  }
  function GroupHost() {
    const [open, setOpen] = React.useState(false), state = useWorker();
    React.useEffect(() => {
      const show = () => setOpen(true); window.addEventListener("opencore-open-groups", show);
      return () => window.removeEventListener("opencore-open-groups", show);
    }, []);
    return h(React.Fragment, null, open ? h(GroupPanel, { close: () => setOpen(false), workerState: state }) : null,
      state.approvals[0] ? h(Approval, { request: state.approvals[0], key: state.approvals[0].requestId }) : null);
  }
  globalThis.__opencoreGroupEntry = GroupEntry;
  globalThis.__opencoreGroupHost = GroupHost;
}
