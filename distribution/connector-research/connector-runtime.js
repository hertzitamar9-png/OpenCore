const $connectorSources = ["claude", "codex", "hermes", "opencode"];
function $uniqueSidebarSections(groups) {
    const seen=new Set(),result={};
    for(const [key,items] of Object.entries(groups))result[key]=items.filter(row=>{
        if(seen.has(row.id))return false;
        seen.add(row.id);return true;
    });
    return {...result,$shown:seen};
}
const $connectorHistoryEvent = "opencore-connector-history-changed";
const $connectorClearEvent = "opencore-connector-clear-changed";
const $connectorClearJobs = new Map();
const $connectorClearFeedback = new Map();
const $connectorSyncWatches = new Map();
let $connectorProfiles = {loaded:false, disconnected:{}};
const $connectorProfileListeners = new Set();
const $connectorDisconnectJobs = new Map();

function $publishConnectorProfiles(value) {
    $connectorProfiles = {...value, loaded:true};
    $connectorProfileListeners.forEach(listener => listener($connectorProfiles));
}

function $useConnectorProfiles() {
    const [value, setValue] = p.useState($connectorProfiles);
    p.useEffect(() => {
        let current = true;
        $connectorProfileListeners.add(setValue);
        $hubRequest('/connectors/status').then(result => {if(current)$publishConnectorProfiles(result);}).catch(() => {});
        return () => {current=false;$connectorProfileListeners.delete(setValue);};
    }, []);
    return value;
}

function $connectorView(row, state) {
    return state.disconnected[row.id] ? {...row, status:'disconnected', observable:false,
        details:'OpenCore profile disconnected. Restart this client if it is already running. Reconnect to add the profile again; delete imported conversations separately.'} : row;
}

function $disconnectConnectorProfile(id) {
    if($connectorDisconnectJobs.has(id))return $connectorDisconnectJobs.get(id);
    const operation=$hubRequest('/connectors/disconnect',{id}).then(result=>{
        $publishConnectorProfiles(result);return result.message;
    }).finally(()=>$connectorDisconnectJobs.delete(id));
    $connectorDisconnectJobs.set(id,operation);return operation;
}

async function $reconnectConnectorProfile(id) {
    const result=await $hubRequest('/connectors/reconnect',{id});
    $publishConnectorProfiles(result);
}

function $notifyConnectorHistory() {
    window.dispatchEvent(new CustomEvent($connectorHistoryEvent));
}

function $watchConnectorSync(operation) {
    if (!operation || operation.kind !== "history_sync" || !["queued", "running"].includes(operation.status) || $connectorSyncWatches.has(operation.id)) return;
    const state = {timer: null};
    $connectorSyncWatches.set(operation.id, state);
    const poll = async () => {
        try {
            const operations = await rb();
            const current = operations.find(row => row.id === operation.id);
            if (!current || !["queued", "running"].includes(current.status)) {
                $connectorSyncWatches.delete(operation.id);
                $notifyConnectorHistory();
                return;
            }
        } catch (_) {}
        state.timer = window.setTimeout(poll, document.hidden ? 15000 : 5000);
    };
    state.timer = window.setTimeout(poll, 5000);
}

function $useConnectorCounts(conversations, revision) {
    const [imported, setImported] = p.useState(null);
    const [generation, setGeneration] = p.useState(0);
    const signature = `${revision}:${JSON.stringify(conversations.map(row => [row.id, row.client, row.updatedAt]))}`;
    p.useEffect(() => {
        const changed = () => setGeneration(value => value + 1);
        window.addEventListener($connectorHistoryEvent, changed);
        rb().then(operations => operations.forEach($watchConnectorSync)).catch(() => {});
        return () => window.removeEventListener($connectorHistoryEvent, changed);
    }, []);
    p.useEffect(() => {
        let current = true;
        Promise.all($connectorSources.map(async source => {
            const result = await Wu("", 0, 1, source);
            return [source, result.total];
        })).then(entries => {
            if (current) setImported(Object.fromEntries(entries));
        }).catch(() => {});
        return () => { current = false; };
    }, [signature, generation]);
    const counts = {$loaded: imported !== null};
    for (const source of $connectorSources) {
        const native = new Set(conversations.filter(row => {
            const client = row.client.toLowerCase();
            return !client.startsWith("imported ") && client.includes(source);
        }).map(row => row.id));
        counts[source] = native.size + (imported?.[source] || 0);
    }
    return counts;
}

function $connectorTabKey(event, tabs, selected, onSelect) {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = tabs.findIndex(([id]) => id === selected);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
        (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    const id = tabs[next][0];
    onSelect(id);
    event.currentTarget.parentElement.querySelector(`#conversation-source-tab-${id}`)?.focus();
}

function $mergeConnectorConversations(nativeItems, page) {
    const native = new Map(nativeItems.map(row => [row.id, row]));
    const imported = new Map((page?.conversations || []).map(row => [row.id, row]));
    const overlap = [...imported.keys()].filter(id => native.has(id)).length;
    const items = [...new Map([...native, ...imported]).values()].sort((left, right) =>
        Number(right.pinned) - Number(left.pinned) || right.updatedAt.localeCompare(left.updatedAt) || left.id.localeCompare(right.id));
    return {items, total: native.size + (page?.total || 0) - overlap};
}

function $emitConnectorClear() {
    window.dispatchEvent(new CustomEvent($connectorClearEvent));
}

function $useConnectorClearState() {
    const [state, setState] = p.useState(() => Object.fromEntries($connectorClearFeedback));
    p.useEffect(() => {
        const changed = () => setState(Object.fromEntries($connectorClearFeedback));
        window.addEventListener($connectorClearEvent, changed);
        changed();
        return () => window.removeEventListener($connectorClearEvent, changed);
    }, []);
    return state;
}

function $forgetConnectorClearFeedback(id) {
    if (!$connectorClearJobs.has(id)) {
        $connectorClearFeedback.delete(id);
        $emitConnectorClear();
    }
}

function $clearConnectorHistory(id) {
    if ($connectorClearJobs.has(id)) return $connectorClearJobs.get(id);
    $connectorClearFeedback.set(id, {busy: true, message: "Removing imported history from OpenCore and ECHO…", error: false});
    const request = Promise.resolve().then(() => globalThis.__opencoreChatDeletion.clear(id)).then(message => {
        $connectorClearFeedback.set(id, {busy: false, message, error: false});
        $notifyConnectorHistory();
        return message;
    }, error => {
        $connectorClearFeedback.set(id, {busy: false, message: String(error), error: true});
        throw error;
    }).finally(() => {
        $connectorClearJobs.delete(id);
        $emitConnectorClear();
    });
    $connectorClearJobs.set(id, request);
    $emitConnectorClear();
    return request;
}

function $refreshConnectorView(refresh, id, message) {
    const entry = $connectorClearFeedback.get(id);
    const update = (text, error = false) => {
        if ($connectorClearFeedback.get(id) !== entry) return;
        entry.message = text;
        entry.error = error;
        $emitConnectorClear();
    };
    const timer = window.setTimeout(() => {
        update(`${message} · The chat list refresh is still pending. Reopen Conversations to retry the view refresh.`);
    }, 8000);
    Promise.resolve().then(refresh).then(() => {
        update(message);
        $notifyConnectorHistory();
    }, error => {
        update(`${message} · The chat list could not refresh: ${String(error)}`, true);
    }).finally(() => window.clearTimeout(timer));
}

function $connectorProfileLabel(status) {
    return {configured: "Profile saved", observed: "Requests observed", detected: "Local files found", offline: "Local files missing", disconnected: "Disconnected"}[status] || status;
}

const $connectorTabStyle = document.createElement("style");
$connectorTabStyle.dataset.opencoreConnectorRepair = "true";
$connectorTabStyle.textContent = `
.conversation-sections[role="tablist"]{display:flex;flex-wrap:nowrap;gap:2px;overflow-x:auto;padding:0 6px;border-bottom:1px solid var(--border,#30333c)}
.conversation-sections[role="tablist"] button.conversation-section-tab{flex:0 0 auto;background:transparent;border:0;border-bottom:2px solid transparent;border-radius:6px 6px 0 0;padding:9px 10px;box-shadow:none;color:var(--muted,#a5a8b1);white-space:nowrap}
.conversation-sections[role="tablist"] button.conversation-section-tab.active{border-bottom-color:var(--accent,#aaaaff);color:var(--text,#ececf4);background:var(--surface-2,rgba(255,255,255,.04))}
.conversation-sections[role="tablist"] button.conversation-section-tab:focus-visible{outline:2px solid var(--accent,#aaaaff);outline-offset:-3px}
.connector-profile-button.primary{min-width:126px}
`;
document.head.appendChild($connectorTabStyle);
