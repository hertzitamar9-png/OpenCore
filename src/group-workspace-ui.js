export function installGroupWorkspaceUI(React, { service, invoke, worker, Dialog, Board, groupReadyModels, resolveGroupTargets }) {
  const h = React.createElement, selectionKey = 'opencore.group.workspace.v2', draftKey = 'opencore.group.drafts.v2', displayKey = 'opencore.chat-core.display.v1';
  const read = (key, fallback) => { try { const value = JSON.parse(localStorage.getItem(key)); return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback; } catch { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const uuid = () => crypto.randomUUID(), status = value => ({ queued: 'Queued', running: 'Working', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' }[value] || value);
  const button = (text, onClick, props = {}) => h('button', { type: 'button', onClick, ...props }, text);
  const field = (label, control, note) => h('label', { className: 'gw-field' }, h('span', null, label), control, note ? h('small', null, note) : null);
  const input = (label, value, onChange, props = {}) => h('input', { 'aria-label': label, value, onChange: event => onChange(event.target.value), ...props });
  const badge = (text, kind = '') => h('span', { className: 'gw-badge ' + kind }, text);
  const list = value => Array.isArray(value) ? value : [];
  const agentDefaults = { enabled: true, reasoningEffort: 'medium', approvalMode: 'inherit', skills: [], subagentsEnabled: true, maxSubagents: 3, historyMessages: 20, allowDelegation: true, acceptDelegation: true, priority: 5 };
  const localDefaults = value => ({ enabled: false, approvalMode: 'ask-every-time', maxSubagents: 3, projectBindings: {}, ...value, models: list(value?.models).map((agent, index) => ({ ...agentDefaults, replyMode: 'mentions', aliases: [], allowedMembers: [], allowedChannels: [], instructions: '', ...agent, key: agent.key || 'legacy_' + index })) });
  const isAPI = model => model.executionKind === 'api' || model.id?.startsWith('api:') || model.backend === 'api' || model.provider;
  const ready = model => !!model && (isAPI(model) || model.ready === true || model.ready !== false && !!model.installed);
  const modelOption = model => (isAPI(model) ? 'Optional API · ' : ready(model) ? 'Local · ' : 'Prepare local · ') + model.label;
  const roleTemplates = {
    Planner: 'Break the requested work into concrete steps, identify dependencies, and update the shared plan. Report completion only with evidence.',
    Coder: 'Implement assigned changes, preserve existing behavior, and report changed files and verification results.',
    Reviewer: 'Review the proposed changes for actionable defects. Cite evidence and suggest concrete fixes.',
    Tester: 'Exercise the requested behavior, record observed results, and report failures with reproduction steps.',
  };

  return function GroupPanel({ close, workerState = {} }) {
    const saved = React.useRef(read(selectionKey, {}));
    const [groups, setGroups] = React.useState([]), [id, setId] = React.useState(saved.current.id || '');
    const [channelId, setChannelId] = React.useState(saved.current.channels?.[saved.current.id] || 'general');
    const [tab, setTab] = React.useState(saved.current.id ? 'Chat' : 'Groups'), [data, setData] = React.useState(null);
    const [mode, setMode] = React.useState(''), [groupName, setGroupName] = React.useState(''), [displayName, setDisplayName] = React.useState('');
    const [joinCode, setJoinCode] = React.useState(''), [invite, setInvite] = React.useState(null), [error, setError] = React.useState(''), [notice, setNotice] = React.useState('');
    const [pending, setPending] = React.useState(false), [models, setModels] = React.useState([]), [localProjects, setLocalProjects] = React.useState([]), [modelError, setModelError] = React.useState('');
    const [sharing, setSharing] = React.useState(null), [dirty, setDirty] = React.useState(false), [addModel, setAddModel] = React.useState('');
    const [drafts, setDrafts] = React.useState(() => read(draftKey, {})), [targets, setTargets] = React.useState({}), [routes, setRoutes] = React.useState({});
    const [channelName, setChannelName] = React.useState(''), [channelProject, setChannelProject] = React.useState(''), [editingChannel, setEditingChannel] = React.useState('');
    const [projectName, setProjectName] = React.useState(''), [editingProject, setEditingProject] = React.useState(''), [localResult, setLocalResult] = React.useState(null);
    const [display, setDisplay] = React.useState(() => ({ density: 'comfortable', showTimestamps: true, showPCNames: true, ...read(displayKey, {}) }));
    const current = React.useRef({ id, channelId }), epoch = React.useRef(0), busy = React.useRef(false), alive = React.useRef(true), messagesRef = React.useRef(null), previousMessages = React.useRef(0);
    current.current = { id, channelId };
    React.useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    React.useEffect(() => {
      const next = { id, channels: { ...saved.current.channels, [id]: channelId } };
      next.channels = Object.fromEntries(Object.entries(next.channels).slice(-32)); saved.current = next; write(selectionKey, next);
    }, [id, channelId]);
    React.useEffect(() => write(draftKey, Object.fromEntries(Object.entries(drafts).filter(([, value]) => value?.text).slice(-64))), [drafts]);
    React.useEffect(() => write(displayKey, display), [display]);

    const select = (nextId, nextChannel = 'general', nextTab = 'Chat') => {
      const groupChanged = nextId !== current.current.id;
      current.current = { id: nextId, channelId: nextChannel }; epoch.current++;
      setId(nextId); setChannelId(nextChannel); setData(null); setTab(nextId ? nextTab : 'Groups'); setMode(''); setError(''); setNotice(''); previousMessages.current = 0;
      if (groupChanged) { setSharing(null); setDirty(false); setInvite(null); }
    };
    const adopt = (value, scope, jump = false) => {
      if (!alive.current || current.current.id !== scope.id) return;
      if (value.localOnly) { setData(old => old ? { ...old, sharing: value.sharing } : old); return; }
      if (!value.group) return;
      const nextChannel = value.channel?.id || scope.channelId;
      if (jump) { if (current.current.channelId !== scope.channelId) return; current.current = { id: scope.id, channelId: nextChannel }; setChannelId(nextChannel); setTab('Chat'); setData(value); previousMessages.current = 0; }
      else if (nextChannel === current.current.channelId) setData(value);
      else setData(old => old ? { ...old, members: value.members || old.members, agents: value.agents || old.agents, projects: value.projects || old.projects, channels: value.channels || old.channels, sharing: value.sharing || old.sharing } : old);
    };
    const act = async (action, body, accept) => {
      if (busy.current) return;
      const scope = { ...current.current }; busy.current = true; epoch.current++; setPending(true); setError(''); setNotice('');
      try { const value = await service(action, body); if (alive.current) accept?.(value, scope); }
      catch (failure) { if (alive.current && current.current.id === scope.id) setError(String(failure)); }
      finally { busy.current = false; epoch.current++; if (alive.current) setPending(false); }
    };
    React.useEffect(() => {
      let live = true, timer;
      const refresh = async () => {
        const scope = { id, channelId }, version = epoch.current;
        try {
          const result = await service('list', {});
          if (!live) return;
          setGroups(list(result.groups)); setDisplayName(old => old || result.localName || '');
          if (id) {
            const view = await service('view', scope);
            if (live && epoch.current === version && current.current.id === id && current.current.channelId === channelId) setData(view);
          }
        } catch (failure) { if (live && epoch.current === version) setError(String(failure)); }
        if (live) timer = setTimeout(refresh, 2000);
      };
      refresh(); return () => { live = false; clearTimeout(timer); };
    }, [id, channelId]);
    const loadModels = React.useCallback(async () => {
      const results = await Promise.allSettled([invoke('list_model_library', {}), invoke('api_provider_list', {}), invoke('get_snapshot', {})]);
      if (!alive.current) return;
      const library = results[0].status === 'fulfilled' ? results[0].value : { models: [] }, providers = results[1].status === 'fulfilled' ? results[1].value : [];
      const available = groupReadyModels(library || { models: [] }, Array.isArray(providers) ? providers : providers?.providers || [], { includeInstallable: true }).sort((a, b) => Number(!!isAPI(a)) - Number(!!isAPI(b)) || Number(ready(b)) - Number(ready(a)));
      setModels(available); setAddModel(old => available.some(model => model.id === old) ? old : available[0]?.id || '');
      setLocalProjects(results[2].status === 'fulfilled' ? list(results[2].value?.projects) : []);
      setModelError(results.filter(result => result.status === 'rejected').map(result => String(result.reason)).join(' · '));
    }, []);
    React.useEffect(() => { loadModels(); }, [loadModels]);
    React.useEffect(() => { if (data?.sharing && !dirty) setSharing(localDefaults(data.sharing)); }, [data?.sharing, dirty]);
    React.useEffect(() => {
      const area = messagesRef.current, count = data?.messages?.length || 0;
      if (area && count !== previousMessages.current && (area.scrollHeight - area.scrollTop - area.clientHeight < 180 || !previousMessages.current)) area.scrollTop = area.scrollHeight;
      previousMessages.current = count;
    }, [data?.messages?.length, tab]);

    const agents = list(data?.agents), members = list(data?.members), channels = list(data?.channels).length ? data.channels : [{ id: 'general', name: 'General' }], projects = list(data?.projects);
    const channel = data?.channel || channels.find(item => item.id === channelId), owner = data?.self?.role === 'owner';
    const selectedGroup = groups.find(group => group.id === id), selectedProject = projects.find(project => project.id === channel?.projectId);
    const key = id + ':' + channelId, draft = drafts[key]?.text || '', route = routes[key] || (channel?.private && !channel.agentIds?.length ? 'humans-only' : 'auto'), selectedTargets = targets[key] || [];
    const allowed = agent => agent.enabled !== false && (!agent.allowedMembers?.length || agent.allowedMembers.includes(data?.self?.id)) && (!agent.allowedChannels?.length || agent.allowedChannels.includes(channelId)) && (!channel?.private || channel.memberIds?.includes(agent.memberId));
    const eligible = agents.filter(allowed), editSharing = patch => { setDirty(true); setSharing(old => ({ ...localDefaults(old), ...patch })); };
    const configured = list(sharing?.models), activeTasks = list(data?.tasks).filter(task => !['completed', 'failed', 'cancelled'].includes(task.status));
    const editAgent = (agentKey, patch) => editSharing({ models: configured.map(agent => agent.key === agentKey ? { ...agent, ...patch } : agent) });
    const addAgent = (model, role) => {
      if (!model || configured.length >= 8) return;
      editSharing({ models: [...configured, { ...agentDefaults, id: model.id, key: uuid(), label: model.label || model.id, catalogId: model.catalogId || model.id, executionKind: isAPI(model) ? 'api' : 'local', prepareIfMissing: false, name: role || (model.label || model.id).slice(0, 100), replyMode: 'mentions', aliases: [], allowedMembers: [], allowedChannels: [], instructions: roleTemplates[role] || '' }] });
    };
    const validSharing = sharing && (!sharing.enabled || configured.length > 0 && configured.every(agent => agent.name?.trim() && models.some(model => model.id === agent.id && (ready(model) || agent.prepareIfMissing === true))));
    const saveSharing = event => {
      event?.preventDefault(); if (!validSharing) return;
      const clean = configured.map(({ _aliases, ...agent }) => ({ ...agent, aliases: (_aliases === undefined ? agent.aliases || [] : _aliases.split(',').map(alias => alias.trim()).filter(Boolean)).slice(0, 8) }));
      act('sharing', { id, channelId, ...sharing, models: clean }, (value, scope) => {
        if (scope.id !== current.current.id) return;
        setDirty(false); setSharing(localDefaults(value.sharing || { ...sharing, models: clean })); adopt(value, scope); setNotice(value.notice || 'Sharing saved for this PC.');
      });
    };
    const createOrJoin = event => {
      event.preventDefault(); const creating = mode === 'create';
      act(creating ? 'create' : 'join', creating ? { name: groupName.trim(), displayName: displayName.trim() } : { invite: joinCode.trim(), displayName: displayName.trim() }, value => {
        setGroups(old => [...old.filter(group => group.id !== value.id), value]); select(value.id); setJoinCode(''); setGroupName('');
      });
    };
    const openChannel = value => select(id, value);
    const openDirect = (memberIds, agentIds) => act('direct', { id, memberIds, agentIds }, (value, scope) => {
      adopt(value, scope, true); const nextKey = scope.id + ':' + value.channel.id;
      setRoutes(old => ({ ...old, [nextKey]: agentIds.length ? 'selected' : 'humans-only' })); setTargets(old => ({ ...old, [nextKey]: agentIds })); setMode('');
    });
    const askAgent = agent => {
      setDrafts(old => ({ ...old, [key]: { text: (old[key]?.text || '') + (old[key]?.text ? ' ' : '') + '@' + agent.name + ' ' } }));
      setTargets(old => ({ ...old, [key]: [...new Set([...(old[key] || []), agent.id])].slice(0, 8) })); setRoutes(old => ({ ...old, [key]: 'selected' })); setTab('Chat');
    };
    const send = event => {
      event?.preventDefault(); if (!draft.trim() || !data || busy.current) return;
      const resolved = route === 'auto' ? resolveGroupTargets(draft, eligible, selectedTargets) : { agentIds: route === 'selected' ? selectedTargets.filter(value => eligible.some(agent => agent.id === value)) : [], error: '' };
      if (resolved.error) { setError(resolved.error); return; }
      if (route === 'selected' && !resolved.agentIds.length) { setError('Choose an agent or select Humans only.'); return; }
      if (resolved.agentIds.length > 8) { setError('Choose up to eight agents for one message.'); return; }
      const signature = JSON.stringify([draft, route, resolved.agentIds]), requestId = drafts[key]?.signature === signature ? drafts[key].requestId : uuid();
      setDrafts(old => ({ ...old, [key]: { ...old[key], text: draft, signature, requestId } }));
      act('message', { id, channelId, text: draft, routing: route, agentIds: resolved.agentIds, requestId }, (value, scope) => {
        adopt(value, scope); setDrafts(old => old[key]?.text === draft ? { ...old, [key]: { text: '' } } : old);
      });
    };
    const startChannel = item => { setChannelName(item?.name || ''); setChannelProject(item?.projectId || ''); setEditingChannel(item?.id || ''); setMode('channel'); setTab('Groups'); };
    const startProject = item => { setProjectName(item?.name || ''); setEditingProject(item?.id || ''); setMode('project'); setTab('Projects'); };
    const projectOptions = [h('option', { key: '', value: '' }, 'No shared project'), ...projects.map(project => h('option', { key: project.id, value: project.id }, project.name))];
    const saveChannel = event => { event.preventDefault(); act('channel-save', { id, name: channelName.trim(), ...(editingChannel ? { channelId: editingChannel } : {}), projectId: channelProject || null }, (value, scope) => { adopt(value, scope, true); setMode(''); }); };
    const saveProject = event => { event.preventDefault(); act('project-save', { id, channelId, name: projectName.trim(), ...(editingProject ? { projectId: editingProject } : {}) }, (value, scope) => { adopt(value, scope); setMode(''); }); };
    const bindProject = (projectId, localId) => { const bindings = { ...sharing?.projectBindings }; if (localId) bindings[projectId] = localId; else delete bindings[projectId]; editSharing({ projectBindings: bindings }); };
    const mapping = project => field('Local project for ' + project.name, h('select', { 'aria-label': 'Local project for ' + project.name, value: sharing?.projectBindings?.[project.id] || '', onChange: event => bindProject(project.id, event.target.value) },
      h('option', { value: '' }, 'No local project'), localProjects.map(local => h('option', { key: local.id, value: local.id }, local.name))), 'Only the project name is shared. Your local folder stays on this PC.');
    const pendingButton = { disabled: pending };
    const editor = mode === 'create' || mode === 'join' ? h('form', { className: 'gw-editor', onSubmit: createOrJoin },
      h('h3', null, mode === 'create' ? 'Create a shared workspace' : 'Join your friends'),
      field('Your name', input('Your name', displayName, setDisplayName, { required: true, maxLength: 64, autoComplete: 'nickname' })),
      mode === 'create' ? field('Group name', input('Group name', groupName, setGroupName, { required: true, maxLength: 100 })) : field('Paste group invite', h('textarea', { 'aria-label': 'Paste group invite', value: joinCode, rows: 3, required: true, spellCheck: false, onChange: event => setJoinCode(event.target.value) })),
      h('div', { className: 'gw-actions' }, button(mode === 'create' ? 'Create group' : 'Join group', null, { type: 'submit', disabled: pending || !displayName.trim() || !(mode === 'create' ? groupName.trim() : joinCode.trim()) }), button('Cancel', () => setMode(''))))
      : mode === 'channel' ? h('form', { className: 'gw-editor', onSubmit: saveChannel }, h('h3', null, editingChannel ? 'Edit channel' : 'Create a channel'),
        field('Channel name', input('Channel name', channelName, setChannelName, { required: true, maxLength: 100 })),
        field('Channel project', h('select', { 'aria-label': 'Channel project', value: channelProject, onChange: event => setChannelProject(event.target.value) }, projectOptions)),
        h('div', { className: 'gw-actions' }, button('Save channel', null, { type: 'submit', disabled: pending || !channelName.trim() }), button('Cancel', () => setMode(''))))
        : mode === 'project' ? h('form', { className: 'gw-editor', onSubmit: saveProject }, h('h3', null, editingProject ? 'Rename shared project' : 'Create a shared project'),
          field('Shared project name', input('Shared project name', projectName, setProjectName, { required: true, maxLength: 100 })), h('div', { className: 'gw-actions' }, button('Save project', null, { type: 'submit', disabled: pending || !projectName.trim() }), button('Cancel', () => setMode('')))) : null;

    const sidebar = h('aside', { className: 'gw-sidebar', 'aria-label': 'Your groups and channels' },
      h('div', { className: 'gw-section-heading' }, h('h2', null, 'Your groups'), badge(groups.length)),
      h('div', { className: 'gw-create-actions' }, button('Create group', () => { setMode(mode === 'create' ? '' : 'create'); setTab('Groups'); }), button('Join with invite', () => { setMode(mode === 'join' ? '' : 'join'); setTab('Groups'); })),
      h('nav', { className: 'gw-group-list', 'aria-label': 'Groups' }, groups.map(group => button(h(React.Fragment, null, h('span', { className: 'gw-group-icon', 'aria-hidden': true }, group.name?.slice(0, 1).toUpperCase()), h('span', null, h('strong', null, group.name), h('small', null, group.hosting ? 'Hosted on this PC' : group.connected ? 'Connected' : 'Reconnecting…'))), () => select(group.id, saved.current.channels?.[group.id] || 'general'), { key: group.id, className: group.id === id ? 'active' : '', 'aria-current': group.id === id ? 'page' : undefined }))),
      !groups.length ? h('p', { className: 'gw-muted' }, 'Create a group or paste an invite to work together.') : null,
      id ? h(React.Fragment, null, h('div', { className: 'gw-section-heading' }, h('h3', null, 'Channels'), button('+', () => startChannel(), { 'aria-label': 'Create channel', disabled: pending })),
        h('nav', { className: 'gw-channel-list', 'aria-label': 'Channels' }, channels.filter(item => !item.private).map(item => button(h(React.Fragment, null, h('span', { 'aria-hidden': true }, '#'), h('span', null, item.name, item.projectId ? h('small', null, projects.find(project => project.id === item.projectId)?.name || 'Shared project') : null)), () => openChannel(item.id), { key: item.id, className: channelId === item.id ? 'active' : '', 'aria-current': channelId === item.id ? 'page' : undefined }))),
        h('div', { className: 'gw-section-heading' }, h('h3', null, 'Direct chats'), button('+', () => { setTab('Agents'); setMode(''); }, { 'aria-label': 'Start a direct chat' })),
        h('nav', { className: 'gw-channel-list', 'aria-label': 'Direct chats' }, channels.filter(item => item.private).map(item => button(h(React.Fragment, null, h('span', { 'aria-hidden': true }, '↗'), h('span', null, item.name)), () => openChannel(item.id), { key: item.id, className: channelId === item.id ? 'active' : '' }))),
        !channels.some(item => item.private) ? h('small', null, 'Open a private chat with a member or agent from Agents.') : null,
        h('div', { className: 'gw-rail-footer' }, h('span', { className: 'gw-presence' }), 'Owner-controlled tools', h('small', null, 'Each agent runs on its named PC.'))) : null);

    const cancelActions = activeTasks.filter(task => owner || task.requestedBy === data?.self?.id || agents.find(agent => agent.id === task.agentId)?.memberId === data?.self?.id).map(task => h('div', { className: 'gw-cancel-row', key: task.id }, h('span', null, h('strong', null, task.agentName || 'Agent'), h('small', null, status(task.status))), button('Cancel task', () => act('cancel', { id, channelId, taskId: task.id }, (value, scope) => adopt(value, scope)), { disabled: pending, 'aria-label': 'Cancel task for ' + (task.agentName || 'Agent') })));
    const attributed = task => { const pc = display.showPCNames && agents.find(agent => agent.id === task.agentId)?.pcName; return pc ? { ...task, agentName: (task.agentName || 'Agent') + ' · ' + pc } : task; };
    const activity = h('div', { className: 'gw-activity' }, h(Board, { data: data ? { ...data, tasks: list(data.tasks).map(attributed), plan: list(data.plan).map(item => { const task = list(data.tasks).find(task => task.id === item.taskId); return task ? { ...item, agentName: attributed(task).agentName } : item; }) } : { tasks: [], plan: [] } }), cancelActions.length ? h('section', { className: 'gw-cancel-actions', 'aria-label': 'Task controls' }, cancelActions) : null);
    const targetControls = agents.length ? h('details', { className: 'group-targets gw-targets' }, h('summary', null, 'Choose agents', selectedTargets.length ? ' · ' + selectedTargets.length + ' selected' : ''),
      h('div', null, agents.map(agent => h('label', { key: agent.id }, h('input', { type: 'checkbox', checked: selectedTargets.includes(agent.id), disabled: !allowed(agent) || !agent.online || route === 'humans-only', 'aria-label': 'Ask ' + agent.name + ' on ' + agent.pcName, onChange: event => { const checked = event.target.checked; setTargets(old => ({ ...old, [key]: checked ? [...new Set([...(old[key] || []), agent.id])].slice(0, 8) : (old[key] || []).filter(value => value !== agent.id) })); } }), h('span', null, '@' + agent.name, h('small', null, agent.pcName + ' · ' + (!allowed(agent) ? 'Restricted here' : agent.online ? agent.modelLabel || agent.modelId : 'Offline'))))))) : null;
    const chat = h('div', { className: 'gw-chat-layout' }, h('section', { className: 'gw-chat', 'aria-label': 'Group conversation' },
      channel?.private ? h('p', { className: 'gw-private-note' }, 'Private to this channel’s participants. Messages are stored on the group host; agent tasks are also stored on the agent owner’s PC.') : null,
      h('div', { className: 'gw-messages', ref: messagesRef, role: 'log', 'aria-label': 'Group messages', 'aria-live': 'polite' }, list(data?.messages).length ? data.messages.map(message => h('article', { key: message.id, className: 'gw-message ' + (message.kind || '') },
        h('div', { className: 'gw-avatar', 'aria-hidden': true }, message.kind === 'agent' ? '✦' : (message.authorName || 'G').slice(0, 1)), h('div', { className: 'gw-message-body' }, h('header', null, h('strong', null, message.authorName || 'Group'), message.kind === 'agent' ? badge(display.showPCNames ? agents.find(agent => agent.id === message.agentId)?.pcName || 'Agent' : 'Agent') : null, display.showTimestamps ? h('time', { dateTime: message.createdAt }, Number.isFinite(Date.parse(message.createdAt)) ? new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '') : null), h('div', { className: 'gw-message-text' }, message.text),
          /opencore-group-local:\/\/conversation\/([^\s]+)/.test(message.text) ? button('Open complete result on this PC', async () => { try { const match = message.text.match(/opencore-group-local:\/\/conversation\/([^\s]+)/); setLocalResult(await worker?.readResult(id, decodeURIComponent(match[1])) || 'The complete result is on the agent owner’s PC.'); } catch (failure) { setError(String(failure)); } }) : null)))
        : h('div', { className: 'gw-chat-welcome' }, h('span', { className: 'gw-channel-mark', 'aria-hidden': true }, channel?.private ? '↗' : '#'), h('h3', null, channel?.name || 'General'), h('p', null, selectedProject ? 'Work together on ' + selectedProject.name + '.' : 'A place for people and agents to work together.'), h('small', null, 'Mention an @agent, choose one below, or send a message to people.'))),
      h('form', { className: 'gw-composer', onSubmit: send }, h('div', { className: 'gw-route-row' }, h('select', { 'aria-label': 'Message routing', value: route, onChange: event => setRoutes(old => ({ ...old, [key]: event.target.value })) }, h('option', { value: 'auto' }, 'Follow agent rules'), h('option', { value: 'selected' }, 'Selected agents only'), h('option', { value: 'humans-only' }, 'Humans only')), targetControls),
        h('div', { className: 'gw-compose-row' }, h('textarea', { 'aria-label': 'Group message', placeholder: channel?.private ? 'Message this private chat…' : 'Message #' + (channel?.name || 'General') + ', or @agent…', rows: 2, maxLength: 16000, value: draft, onChange: event => { const text = event.target.value; setDrafts(old => ({ ...old, [key]: { text } })); }, onKeyDown: event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } } }), button(pending ? 'Sending…' : 'Send', null, { type: 'submit', 'aria-label': 'Send group message', disabled: pending || !draft.trim() })),
        h('small', { className: 'gw-compose-hint' }, route === 'humans-only' ? 'Only people receive this message. Agents will not be started.' : route === 'selected' ? 'Only checked agents receive a task. Each uses its owner’s tool policy.' : 'Agent owners choose reply rules. Busy PCs queue work.'))), h('aside', { className: 'gw-plan-sidebar', 'aria-label': 'Channel activity' }, activity));

    const people = h('div', { className: 'gw-people' }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'People & agents'), h('p', null, 'Talk directly, or bring an agent into this channel.')), button('Configure my agents', () => setTab('Share my PC'))),
      h('div', { className: 'gw-agent-grid' }, agents.map(agent => h('article', { className: 'gw-person-card', key: agent.id }, h('header', null, h('span', { className: 'gw-avatar agent', 'aria-hidden': true }, '✦'), h('div', null, h('h3', null, '@' + agent.name), h('small', null, agent.modelLabel || agent.modelId)), badge(agent.online ? 'Online' : 'Offline', agent.online ? 'online' : '')),
        h('p', null, agent.pcName), h('small', null, ({ mentions: 'Replies to mentions', always: 'Replies to every human message', manual: 'Replies when selected' }[agent.replyMode] || 'Replies to mentions') + (agent.aliases?.length ? ' · Also @' + agent.aliases.join(', @') : '')),
        agent.allowedMembers?.length || agent.allowedChannels?.length ? h('small', null, 'Access is restricted by this agent’s owner.') : null,
        h('div', { className: 'gw-actions' }, button('Ask agent', () => askAgent(agent), { disabled: !agent.online || !allowed(agent), 'aria-label': 'Ask agent ' + agent.name + ' on ' + agent.pcName }), button('Direct chat', () => openDirect([], [agent.id]), { disabled: pending, 'aria-label': 'Direct chat with ' + agent.name + ' on ' + agent.pcName }))))),
      !agents.length ? h('p', { className: 'gw-empty' }, 'No agents are shared yet. Each PC owner can add local or configured API agents in Share my PC.') : null,
      h('h3', null, 'Members'), h('div', { className: 'gw-member-list' }, members.map(member => h('article', { className: 'gw-member', key: member.id }, h('span', { className: 'gw-avatar', 'aria-hidden': true }, member.name?.slice(0, 1)), h('div', null, h('strong', null, member.name + (member.id === data?.self?.id ? ' (you)' : '')), h('small', null, member.pcName + ' · ' + (member.online ? 'Online' : 'Offline'))), member.id !== data?.self?.id ? h('div', { className: 'gw-actions' }, button('Direct chat', () => openDirect([member.id], []), { disabled: pending, 'aria-label': 'Direct chat with ' + member.name }), owner ? button('Remove', () => { if (confirm('Remove ' + member.name + ' from this group?')) act('revoke', { id, memberId: member.id }, (value, scope) => adopt(value, scope)); }, { disabled: pending, 'aria-label': 'Remove ' + member.name + ' from group' }) : null) : badge(member.role === 'owner' ? 'Host' : 'Member')))));

    const restrict = (agent, label, property, options) => h('fieldset', { className: 'gw-restrictions' }, h('legend', null, label), h('small', null, 'No checked items means everyone or every available channel.'), options.map(option => h('label', { key: option.id }, h('input', { type: 'checkbox', checked: list(agent[property]).includes(option.id), 'aria-label': label + ' ' + option.name + ' for ' + agent.name, onChange: event => editAgent(agent.key, { [property]: event.target.checked ? [...new Set([...list(agent[property]), option.id])] : list(agent[property]).filter(value => value !== option.id) }) }), option.name)));
    const advancedAgent = agent => {
      const toggle = (label, property) => h('label', { className: 'gw-check', key: property }, h('input', { type: 'checkbox', 'aria-label': label + ' for ' + agent.name, checked: agent[property] !== false, onChange: event => editAgent(agent.key, { [property]: event.target.checked }) }), label);
      const number = (label, property, maximum, fallback) => field(label, input(label + ' for ' + agent.name, agent[property] ?? fallback, value => editAgent(agent.key, { [property]: Math.max(property === 'maxSubagents' ? 1 : 0, Math.min(maximum, Math.floor(Number(value) || 0))) }), { type: 'number', min: property === 'maxSubagents' ? 1 : 0, max: maximum }));
      return h('details', { className: 'gw-agent-advanced' }, h('summary', null, 'Advanced settings'), h('div', { className: 'gw-form-grid' },
        toggle('Enable this role', 'enabled'),
        field('Reasoning effort', h('select', { 'aria-label': 'Reasoning effort for ' + agent.name, value: agent.reasoningEffort || 'medium', onChange: event => editAgent(agent.key, { reasoningEffort: event.target.value }) }, ['off', 'low', 'medium', 'high', 'ultra'].map(value => h('option', { key: value, value }, value[0].toUpperCase() + value.slice(1))))),
        field('Role tool approval', h('select', { 'aria-label': 'Role tool approval for ' + agent.name, value: agent.approvalMode || 'inherit', onChange: event => editAgent(agent.key, { approvalMode: event.target.value }) }, h('option', { value: 'inherit' }, 'Use this PC’s group policy'), h('option', { value: 'ask-every-time' }, 'Ask for every tool action'), h('option', { value: 'allow-chat' }, 'Allow tools for this role’s tasks'), h('option', { value: 'allow-all' }, 'Allow all enabled tools'))),
        toggle('Enable subagents', 'subagentsEnabled'), number('Maximum role subagents', 'maxSubagents', 8, 3), number('Recent channel messages', 'historyMessages', 100, 20), number('Task priority', 'priority', 10, 5), toggle('Allow delegating to other agents', 'allowDelegation'), toggle('Accept delegated tasks', 'acceptDelegation')),
        h('fieldset', { className: 'gw-restrictions' }, h('legend', null, 'Tools on this PC'), [['computer-use', 'This PC’s apps'], ['browser-use', 'In-app browser'], ['chrome-control', 'Paired Chrome']].map(([skill, label]) => h('label', { key: skill }, h('input', { type: 'checkbox', 'aria-label': label + ' for ' + agent.name, checked: list(agent.skills).includes(skill), onChange: event => editAgent(agent.key, { skills: event.target.checked ? [...new Set([...list(agent.skills), skill])] : list(agent.skills).filter(value => value !== skill) }) }), label))),
        h('small', null, 'Tools are off until selected and still use this role’s approval policy. A role’s subagent limit is capped by this PC’s group limit. History: 0 uses only the task. Priority: 10 is highest.'));
    };
    const agentEditors = configured.map((agent, index) => {
      const model = models.find(value => value.id === agent.id), number = configured.slice(0, index).filter(value => value.id === agent.id).length + 1, label = model?.label || agent.label || agent.id;
      return h('article', { key: agent.key, className: 'gw-agent-editor' }, h('header', null, h('div', null, h('strong', null, agent.name || 'Unnamed agent'), h('small', null, label)), badge(isAPI(model || { id: agent.id }) ? 'API' : 'Local'), h('div', { className: 'gw-actions' }, button('Duplicate', () => editSharing({ models: [...configured, { ...agent, key: uuid(), name: (agent.name + ' copy').slice(0, 100) }] }), { disabled: configured.length >= 8, 'aria-label': 'Duplicate agent ' + agent.name }), button('Remove', () => editSharing({ models: configured.filter(value => value.key !== agent.key) }), { 'aria-label': 'Remove agent ' + agent.name }))),
        !model ? h('p', { className: 'gw-error' }, 'This model is unavailable in this PC’s supported catalog. Choose another model or remove this agent.') : null,
        h('div', { className: 'gw-form-grid' }, field('Agent name', input('Agent name for ' + label + (number > 1 ? ' #' + number : ''), agent.name || '', value => editAgent(agent.key, { name: value }), { required: true, maxLength: 100 })),
          field('Model on this PC', h('select', { 'aria-label': 'Model for ' + agent.name, value: agent.id, onChange: event => { const next = models.find(value => value.id === event.target.value); if (next) editAgent(agent.key, { id: next.id, label: next.label, catalogId: next.catalogId || next.id, executionKind: isAPI(next) ? 'api' : 'local', prepareIfMissing: false }); } }, !model ? h('option', { value: agent.id }, label + ' (unavailable)') : null, models.map(value => h('option', { key: value.id, value: value.id }, modelOption(value))))),
          field('When this agent replies', h('select', { 'aria-label': 'Reply policy for ' + agent.name, value: agent.replyMode || 'mentions', onChange: event => editAgent(agent.key, { replyMode: event.target.value }) }, h('option', { value: 'mentions' }, 'When mentioned'), h('option', { value: 'always' }, 'Every human message'), h('option', { value: 'manual' }, 'When explicitly selected'))),
          field('Mention aliases', input('Mention aliases for ' + agent.name, agent._aliases ?? list(agent.aliases).join(', '), value => editAgent(agent.key, { _aliases: value }), { maxLength: 390, placeholder: 'coder, reviewer' }))),
        model && !ready(model) ? h('label', { className: 'gw-prepare-choice' }, h('input', { type: 'checkbox', checked: agent.prepareIfMissing === true, 'aria-label': 'Install and prepare ' + label + ' for ' + agent.name, onChange: event => editAgent(agent.key, { prepareIfMissing: event.target.checked }) }), h('span', null, 'Install and prepare this model before its first task', h('small', null, 'Uses the normal OpenCore installer on this PC. Work waits for the model to be ready.'))) : model ? h('small', null, isAPI(model) ? 'Configured API profile · optional' : 'Ready on this PC') : null,
        field('Instructions for this role', h('textarea', { 'aria-label': 'Instructions for ' + agent.name, value: agent.instructions || '', maxLength: 2000, rows: 3, onChange: event => editAgent(agent.key, { instructions: event.target.value }), placeholder: 'Describe the work this agent should own.' }), 'Instructions stay on this PC; other members see the agent’s name and reply rules.'),
        h('details', { className: 'gw-agent-access' }, h('summary', null, 'Access restrictions', agent.allowedMembers?.length || agent.allowedChannels?.length ? ' · Restricted' : ' · All members and channels'), h('div', { className: 'gw-form-grid' }, restrict(agent, 'Allowed people', 'allowedMembers', members), restrict(agent, 'Allowed channels', 'allowedChannels', channels))), advancedAgent(agent));
    });
    const projectMappings = h('div', { className: 'gw-project-mappings' }, projects.map(project => h('div', { key: project.id }, mapping(project))));
    const sharingPanel = sharing ? h('form', { className: 'gw-sharing', onSubmit: saveSharing }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'Your PC, your agents'), h('p', null, 'Choose supported local models and give each a role. One model can fill several roles; configured API profiles are optional.')), button('Refresh models', loadModels, pendingButton)),
      h('label', { className: 'gw-sharing-toggle' }, h('input', { type: 'checkbox', 'aria-label': 'Share models from this PC', checked: !!sharing.enabled, onChange: event => editSharing({ enabled: event.target.checked }) }), h('span', null, h('strong', null, 'Share models from this PC'), h('small', null, sharing.enabled ? 'Enabled for the agents you save below.' : 'Off. Group members cannot start an agent on this PC.'))),
      modelError ? h('p', { className: 'gw-error' }, modelError) : null,
      h('div', { className: 'gw-add-agent' }, field('Model for new agent', h('select', { 'aria-label': 'Model for new agent', value: addModel, onChange: event => setAddModel(event.target.value) }, !models.length ? h('option', { value: '' }, 'No supported text models available') : null, models.map(model => h('option', { key: model.id, value: model.id }, modelOption(model))))), button('Add agent', () => addAgent(models.find(model => model.id === addModel)), { disabled: !addModel || configured.length >= 8 })),
      h('section', { className: 'gw-role-templates', 'aria-label': 'Quick role templates' }, h('small', null, 'Start with a role, then change its name, model or instructions.'), h('div', { className: 'gw-actions' }, Object.keys(roleTemplates).map(role => button(role, () => addAgent(models.find(model => model.id === addModel), role), { key: role, disabled: !addModel || configured.length >= 8, 'aria-label': 'Add ' + role + ' role' })))),
      h('details', { className: 'gw-quick-models', open: !configured.length }, h('summary', null, 'Quick selection · ready models'), models.filter(ready).map(model => h('label', { key: model.id }, h('input', { type: 'checkbox', 'aria-label': 'Share ' + model.label, checked: configured.some(agent => agent.id === model.id), onChange: event => event.target.checked ? addAgent(model) : editSharing({ models: configured.filter(agent => agent.id !== model.id) }) }), model.label, badge(isAPI(model) ? 'API' : 'Local')))),
      configured.some(agent => isAPI(models.find(model => model.id === agent.id) || agent)) ? h('small', null, 'API agents send shared task context to their configured provider. API credentials stay on this PC.') : null,
      !models.length ? h('p', { className: 'gw-empty' }, 'Refresh models to find supported local models. Optional API profiles can be configured in OpenCore.') : null,
      h('div', { className: 'gw-agent-editors' }, agentEditors),
      h('section', { className: 'gw-permissions' }, h('h3', null, 'Tool access on this PC'), h('p', null, 'Tools run under your selected policy on the executing PC. Another group member cannot change this setting.'),
        h('div', { className: 'gw-form-grid' }, field('Tools requested by group agents', h('select', { 'aria-label': 'Group tool approval', value: sharing.approvalMode, onChange: event => editSharing({ approvalMode: event.target.value }) }, h('option', { value: 'ask-every-time' }, 'Ask me before every tool action'), h('option', { value: 'allow-chat' }, 'Allow tools for these group tasks'), h('option', { value: 'allow-all' }, 'Allow all tools for shared agents'))), field('Maximum local subagents', input('Maximum local subagents', sharing.maxSubagents, value => editSharing({ maxSubagents: Math.max(1, Math.min(8, Math.floor(Number(value) || 1))) }), { type: 'number', min: 1, max: 8 }))),
        sharing.approvalMode !== 'ask-every-time' ? h('p', { className: 'gw-permission-note' }, 'These agents may use this PC’s enabled tools without asking for every action. The selected policy applies only after you save it.') : null),
      projects.length ? h('section', null, h('h3', null, 'Shared project → your local project'), projectMappings) : null,
      h('div', { className: 'gw-save-bar' }, h('small', null, dirty ? 'You have unsaved changes.' : 'Only this PC’s sharing settings appear here.'), button('Save sharing', null, { type: 'submit', disabled: pending || !dirty || !validSharing })), !validSharing ? h('p', { className: 'gw-error' }, 'Name each role and choose a ready model, or allow its installation and preparation before the first task.') : null) : h('p', { className: 'gw-empty' }, 'Loading this PC’s sharing settings…');
    const teamPanel = h('section', { className: 'gw-teams' }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'Build your task team'), h('p', null, 'Combine roles, models and PCs in any arrangement. One PC can host several roles and queues its work; different PCs can work concurrently.')), button('Add roles on my PC', () => setTab('Share my PC'))),
      h('p', { className: 'gw-muted' }, 'Choose shared agents for the next task in this channel. Each PC owner controls their models and tool permissions. Local models are primary; API profiles are optional.'),
      h('div', { className: 'gw-pc-grid' }, members.map(member => { const roles = agents.filter(agent => agent.memberId === member.id); return h('article', { key: member.id, className: 'gw-pc-card' }, h('header', null, h('div', null, h('h3', null, member.pcName), h('small', null, member.name + ' · ' + roles.length + (roles.length === 1 ? ' role' : ' roles'))), badge(member.online ? 'Online' : 'Offline', member.online ? 'online' : '')),
        roles.length ? roles.map(agent => h('label', { key: agent.id, className: 'gw-team-role' }, h('input', { type: 'checkbox', 'aria-label': 'Use ' + agent.name + ' on ' + agent.pcName + ' in task team', checked: selectedTargets.includes(agent.id), disabled: !allowed(agent) || !agent.online, onChange: event => { const checked = event.target.checked; setTargets(old => ({ ...old, [key]: checked ? [...new Set([...(old[key] || []), agent.id])].slice(0, 8) : (old[key] || []).filter(value => value !== agent.id) })); } }), h('span', null, h('strong', null, agent.name), h('small', null, agent.modelLabel || agent.modelId), h('small', null, agent.enabled === false ? 'Disabled by owner' : !allowed(agent) ? 'Restricted in this channel' : agent.replyMode === 'manual' ? 'Explicit selection' : agent.replyMode === 'always' ? 'Every human message' : 'Mentions')))) : h('p', { className: 'gw-muted' }, member.id === data?.self?.id ? 'Add roles from this PC in Share my PC.' : 'This member has not shared a role yet.'), member.id === data?.self?.id ? button('Configure this PC’s roles', () => setTab('Share my PC')) : null); })),
      h('div', { className: 'gw-save-bar' }, h('span', null, selectedTargets.length + ' selected roles · ' + new Set(agents.filter(agent => selectedTargets.includes(agent.id)).map(agent => agent.memberId)).size + ' PCs'), button('Compose for this team', () => { setRoutes(old => ({ ...old, [key]: 'selected' })); setTab('Chat'); }, { disabled: !selectedTargets.length })));
    const preferences = h('section', { className: 'gw-preferences' }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'Advanced workspace preferences'), h('p', null, 'These display settings stay on this device and do not change shared group data.'))),
      field('Message density', h('select', { 'aria-label': 'Message density', value: display.density, onChange: event => setDisplay(old => ({ ...old, density: event.target.value })) }, h('option', { value: 'comfortable' }, 'Comfortable'), h('option', { value: 'compact' }, 'Compact'))),
      h('label', { className: 'gw-check' }, h('input', { type: 'checkbox', 'aria-label': 'Show message timestamps', checked: display.showTimestamps, onChange: event => setDisplay(old => ({ ...old, showTimestamps: event.target.checked })) }), 'Show message timestamps'),
      h('label', { className: 'gw-check' }, h('input', { type: 'checkbox', 'aria-label': 'Show PC names in messages and activity', checked: display.showPCNames, onChange: event => setDisplay(old => ({ ...old, showPCNames: event.target.checked })) }), 'Show PC names in messages and activity'),
      h('p', { className: 'gw-muted' }, 'Role behavior is configured under Share my PC → Advanced settings. Channel access, project routing and private-chat membership remain enforced by the group service.'));
    const projectPanel = h('section', { className: 'gw-projects' }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'Shared projects'), h('p', null, 'Organize channels around a project. Every PC owner maps the name to their own existing local project.')), owner ? button('Create project', () => startProject()) : null),
      mode === 'project' ? editor : null, !projects.length ? h('p', { className: 'gw-empty' }, owner ? 'Create a named project, then connect a channel to it.' : 'The group owner can create shared project names.') : projects.map(project => h('article', { className: 'gw-project-card', key: project.id }, h('header', null, h('h3', null, project.name), owner ? button('Rename', () => startProject(project), { 'aria-label': 'Rename project ' + project.name }) : null), h('small', null, channels.filter(item => item.projectId === project.id).length + ' linked channels'), mapping(project))),
      dirty ? h('div', { className: 'gw-save-bar' }, h('small', null, 'Save this PC’s project mappings.'), button('Save sharing', saveSharing, { disabled: pending || !validSharing })) : null);

    return h(Dialog, { label: 'Chat Core', className: 'group-workspace gw-density-' + display.density, close },
      h('header', { className: 'gw-header' }, h('div', { className: 'gw-brand' }, h('img', { src: '/opencore-logo.png', alt: 'OpenCore', width: 36, height: 36 }), h('div', null, h('h1', null, 'Chat Core'), h('p', null, 'People, local models and shared roles'))), button('×', close, { className: 'gw-close', 'aria-label': 'Close Chat Core' })),
      h('div', { className: 'gw-shell' }, h('div', { className: 'gw-desktop-rail' }, sidebar), h('main', { className: 'gw-main' },
        h('header', { className: 'gw-main-header' }, h('div', null, h('h2', null, data?.group?.name || selectedGroup?.name || 'Your groups'), h('small', null, selectedGroup ? selectedGroup.hosting ? 'Hosted on this PC' : selectedGroup.connected ? 'Connected to host' : 'Reconnecting to host…' : 'Create or join a group to get started')), id ? h('div', { className: 'group-header-actions gw-actions' }, owner ? button('Create invite', () => act('invite', { id }, value => setInvite(value)), pendingButton) : null, button('Leave', () => { if (confirm('Leave this group?')) act('leave', { id }, () => { setGroups(old => old.filter(group => group.id !== id)); select(''); }); }, { disabled: pending || owner, title: owner ? 'The hosting PC cannot leave its own group' : undefined })) : null),
        h('nav', { className: 'group-tabs gw-tabs', role: 'tablist', 'aria-label': 'Group views' }, ['Groups', 'Chat', 'Team builder', 'Agents', 'Plan', 'Projects', 'Share my PC', 'Advanced'].map(value => button(value, () => { setTab(value); if (value !== 'Groups' && mode !== 'project') setMode(''); }, { key: value, role: 'tab', 'aria-selected': value === tab, disabled: value !== 'Groups' && !id }))),
        error ? h('p', { className: 'gw-banner gw-error', role: 'alert' }, error) : null, notice ? h('p', { className: 'gw-banner', role: 'status' }, notice) : null,
        workerState.task ? h('p', { className: 'gw-banner' }, 'This PC is working · ' + (workerState.task.agentName || workerState.task.modelId || 'Group agent')) : null, workerState.error ? h('p', { className: 'gw-banner gw-error' }, workerState.error) : null,
        invite ? h('section', { className: 'gw-invite' }, field('Group invite', input('Group invite', invite.invite, () => {}, { readOnly: true })), button('Copy invite', async () => { try { await navigator.clipboard.writeText(invite.invite); setNotice('Invite copied.'); } catch { setNotice('Select and copy the invite above.'); } }), button('Dismiss invite', () => setInvite(null)), h('small', null, 'One-time invite · expires in ' + Math.ceil((invite.expiresIn || 300) / 60) + ' minutes. Your friend must be able to reach this group’s host.')) : null,
        id && tab === 'Chat' ? h('div', { className: 'gw-channel-heading' }, h('span', { className: 'gw-channel-prefix', 'aria-hidden': true }, channel?.private ? '↗' : '#'), h('select', { 'aria-label': 'Current channel', value: channelId, onChange: event => openChannel(event.target.value) }, channels.map(item => h('option', { key: item.id, value: item.id }, (item.private ? 'Private · ' : '# ') + item.name))), selectedProject ? badge(selectedProject.name) : null, !channel?.private && (owner || channel?.createdBy === data?.self?.id) ? button('Edit', () => startChannel(channel), { 'aria-label': 'Edit current channel' }) : null, button('Activity' + (activeTasks.length ? ' · ' + activeTasks.length : ''), () => setTab('Plan'), { className: 'gw-mobile-activity' })) : null,
        h('div', { className: 'gw-content gw-content-' + tab.toLowerCase().replace(/\s/g, '-') }, tab === 'Groups' ? h('div', { className: 'gw-groups-view' }, h('div', { className: 'gw-mobile-rail' }, sidebar), h('section', { className: 'gw-overview' }, mode && mode !== 'project' ? editor : h(React.Fragment, null, h('div', { className: 'gw-overview-mark', 'aria-hidden': true }, '◈'), h('h3', null, id ? 'One workspace. Many ways to work.' : 'Bring your people and agents together.'), h('p', null, id ? 'Choose a channel, start a direct chat, or give shared agents a task. Everyone keeps control of their own PC.' : 'Create a group to host a shared workspace, or join a friend’s invite.'), id ? h('div', { className: 'gw-overview-cards' }, button(h(React.Fragment, null, h('strong', null, channels.filter(item => !item.private).length + ' channels'), h('small', null, 'Projects and team conversations')), () => setTab('Chat')), button(h(React.Fragment, null, h('strong', null, agents.length + ' shared agents'), h('small', null, 'Local and configured API models')), () => setTab('Agents')), button(h(React.Fragment, null, h('strong', null, activeTasks.length + ' active tasks'), h('small', null, 'Plans, tools and progress')), () => setTab('Plan'))) : null)))
          : !data ? h('p', { className: 'gw-empty' }, 'Loading workspace…') : tab === 'Chat' ? chat : tab === 'Team builder' ? teamPanel : tab === 'Agents' ? people : tab === 'Plan' ? activity : tab === 'Projects' ? projectPanel : tab === 'Advanced' ? preferences : sharingPanel))),
      localResult !== null ? h(Dialog, { label: 'Complete local group result', className: 'gw-local-result', close: () => setLocalResult(null) }, h('header', { className: 'gw-header' }, h('h2', null, 'Complete result on this PC'), button('Close', () => setLocalResult(null))), h('pre', null, localResult)) : null);
  };
}
