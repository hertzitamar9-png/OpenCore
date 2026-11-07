export function installGroupWorkspaceUI(React, { service, invoke, worker, Dialog, EmbeddedWorkspace, Board, groupReadyModels, resolveGroupTargets, Conversation }) {
  const h = React.createElement, selectionKey = 'opencore.group.workspace.v2', draftKey = 'opencore.group.drafts.v2', displayKey = 'opencore.chat-core.display.v1';
  const read = (key, fallback) => { try { const value = JSON.parse(localStorage.getItem(key)); return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback; } catch { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const uuid = () => crypto.randomUUID(), status = value => ({ queued: 'Queued', running: 'Working', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' }[value] || value);
  const button = (text, onClick, props = {}) => h('button', { type: 'button', onClick, ...props }, text);
  const field = (label, control, note) => h('label', { className: 'gw-field' }, h('span', null, label), control, note ? h('small', null, note) : null);
  const input = (label, value, onChange, props = {}) => h('input', { 'aria-label': label, value, onChange: event => onChange(event.target.value), ...props });
  const badge = (text, kind = '') => h('span', { className: 'gw-badge ' + kind }, text);
  const list = value => Array.isArray(value) ? value : [];
  const efforts = [['off','Off'],['low','Low'],['medium','Medium'],['high','High'],['extra-high','Extra high'],['max','Max'],['opencore','OpenCore']];
  const approvals = [['ask-every-time','Ask','Ask every time'],['approve-for-me','Auto','Approve for me'],['allow-chat','Chat','Allow everything in this chat'],['allow-all','All','Allow everything']];
  const savedValue = (key, allowed, fallback) => { try { const value=localStorage.getItem(key); return allowed.includes(value) ? value : fallback; } catch { return fallback; } };
  const navigationIcons = {
    groups: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    people: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm4-4a4 4 0 0 1 0 8',
    tasks: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zm5 7 3 3 5-6',
    projects: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
    settings: 'M4 7h16M4 17h16M8 4v6m8 4v6',
    direct: 'M20.5 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.3 0-2.5-.3-3.6-.8L4 20l.8-4.4a8.5 8.5 0 1 1 15.7-4.1Z',
    channel: 'M5 9h15M4 15h15M11 4 7 20m10-16-4 16',
    plus: 'M12 5v14M5 12h14',
    browse: 'M4 6h16M4 12h16M4 18h16',
    back: 'm12 5-7 7 7 7M5 12h15',
  };
  const navIcon = name => h('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
  h('path', { d: navigationIcons[name] }));
  const settingsPages = [['Advanced', 'Display'], ['Share my PC', 'Agents on this PC'], ['Membership', 'Membership']];
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

  function WorkspacePanel({ close, embedded = false, workerState = {} }) {
    const saved = React.useRef(read(selectionKey, {}));
    const [groups, setGroups] = React.useState([]), [id, setId] = React.useState(saved.current.id || '');
    const [channelId, setChannelId] = React.useState(saved.current.channels?.[saved.current.id] || 'general');
    const [tab, setTab] = React.useState(saved.current.id ? 'Chat' : 'Groups'), [data, setData] = React.useState(null);
    const [mode, setMode] = React.useState(''), [groupName, setGroupName] = React.useState(''), [displayName, setDisplayName] = React.useState('');
    const [joinCode, setJoinCode] = React.useState(''), [invite, setInvite] = React.useState(null), [error, setError] = React.useState(''), [refreshError, setRefreshError] = React.useState(''), [notice, setNotice] = React.useState('');
    const [pending, setPending] = React.useState(false), [models, setModels] = React.useState([]), [localProjects, setLocalProjects] = React.useState([]), [modelError, setModelError] = React.useState('');
    const [sharing, setSharing] = React.useState(null), [dirty, setDirty] = React.useState(false), [addModel, setAddModel] = React.useState('');
    const [drafts, setDrafts] = React.useState(() => read(draftKey, {})), [targets, setTargets] = React.useState({}), [routes, setRoutes] = React.useState({});
    const [requestOptions, setRequestOptions] = React.useState({}), [mentions, setMentions] = React.useState({});
    const [mentionMenu, setMentionMenu] = React.useState(null), [mentionIndex, setMentionIndex] = React.useState(0), [channelMenu, setChannelMenu] = React.useState(false);
    const [confirmAction, setConfirmAction] = React.useState('');
    const [navigationOpen, setNavigationOpen] = React.useState(false);
    const [channelName, setChannelName] = React.useState(''), [channelProject, setChannelProject] = React.useState(''), [editingChannel, setEditingChannel] = React.useState('');
    const [projectName, setProjectName] = React.useState(''), [editingProject, setEditingProject] = React.useState(''), [localResult, setLocalResult] = React.useState(null);
    const [display, setDisplay] = React.useState(() => ({ density: 'comfortable', showTimestamps: true, showPCNames: true, ...read(displayKey, {}) }));
    const navigationId = React.useId();
    const browseButton = React.useRef(null), closeNavigationButton = React.useRef(null), navigationWasOpen = React.useRef(false);
    const current = React.useRef({ id, channelId }), epoch = React.useRef(0), busy = React.useRef(false), alive = React.useRef(true), messagesRef = React.useRef(null), composerRef = React.useRef(null), pendingCaret = React.useRef(null), previousMessages = React.useRef(0);
    current.current = { id, channelId };
    React.useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    React.useEffect(() => {
      const next = { id, channels: { ...saved.current.channels, [id]: channelId } };
      next.channels = Object.fromEntries(Object.entries(next.channels).slice(-32)); saved.current = next; write(selectionKey, next);
    }, [id, channelId]);
    React.useEffect(() => write(draftKey, Object.fromEntries(Object.entries(drafts).filter(([, value]) => value?.text).slice(-64))), [drafts]);
    React.useEffect(() => write(displayKey, display), [display]);
    React.useEffect(() => {
      if (navigationOpen) closeNavigationButton.current?.focus();
      else if (navigationWasOpen.current) browseButton.current?.focus();
      navigationWasOpen.current = navigationOpen;
    }, [navigationOpen]);

    const openView = next => {
      setTab(next);
      setMode('');
      setChannelMenu(false);
      setConfirmAction('');
      setNavigationOpen(false);
    };

    const select = (nextId, nextChannel = 'general', nextTab = 'Chat') => {
      const groupChanged = nextId !== current.current.id;
      current.current = { id: nextId, channelId: nextChannel }; epoch.current++;
      setId(nextId); setChannelId(nextChannel); setData(null); openView(nextId ? nextTab : 'Groups'); setError(''); setNotice(''); previousMessages.current = 0;
      setMentionMenu(null); setChannelMenu(false); setConfirmAction('');
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
          if (live && epoch.current === version) setRefreshError('');
        } catch (failure) { if (live && epoch.current === version) setRefreshError(String(failure)); }
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
    React.useLayoutEffect(() => {
      if (pendingCaret.current === null || !composerRef.current) return;
      const position = pendingCaret.current; pendingCaret.current = null;
      composerRef.current.focus(); composerRef.current.setSelectionRange(position, position);
    }, [draft, key]);
    const options = requestOptions[key] || { effort: savedValue('opencore.reasoning-effort.v1',efforts.map(item=>item[0]),'off'), approval: savedValue('opencore.approval-global.v1',['allow-all'],'ask-every-time') };
    const setOption = (field, value) => setRequestOptions(old => ({ ...old, [key]: { ...(old[key] || options), [field]: value } }));
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
        setGroups(old => [...old.filter(group => group.id !== value.id), value]); select(value.id, value.channelId || 'general'); setJoinCode(''); setGroupName('');
      });
    };
    const openChannel = value => select(id, value);
    const openDirect = (memberIds, agentIds) => act('direct', { id, memberIds, agentIds }, (value, scope) => {
      adopt(value, scope, true); const nextKey = scope.id + ':' + value.channel.id;
      setRoutes(old => ({ ...old, [nextKey]: agentIds.length ? 'selected' : 'humans-only' })); setTargets(old => ({ ...old, [nextKey]: agentIds })); setMode('');
    });
    const askAgent = agent => {
      setDrafts(old => ({ ...old, [key]: { text: (old[key]?.text || '') + (old[key]?.text ? ' ' : '') + '@' + agent.name + ' ' } }));
      setMentions(old => ({ ...old, [key]: [...(old[key] || []).filter(item => item.id !== agent.id), { id: agent.id, name: agent.name }] })); setRoutes(old => ({ ...old, [key]: 'auto' })); setTab('Chat');
    };
    const mentionPresent = (text, name) => new RegExp('(^|\\s)@' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?=\\s|$|[.,!?])', 'i').test(text);
    const mentionCandidates = mentionMenu ? eligible.filter(agent => [agent.name, ...(agent.aliases || [])].some(name => name.toLocaleLowerCase().startsWith(mentionMenu.query.toLocaleLowerCase()))).slice(0, 12) : [];
    const changeDraft = (text, cursor = text.length) => {
      setDrafts(old => ({ ...old, [key]: { text } }));
      const match = text.slice(0, cursor).match(/(^|\s)@([^@\n]{0,80})$/);
      const query = match?.[2] || '';
      const partial = eligible.some(agent => [agent.name, ...(agent.aliases || [])].some(name => name.toLocaleLowerCase().startsWith(query.toLocaleLowerCase())));
      setMentionMenu(match && (!/\s/.test(query) || partial) ? { start: cursor - query.length - 1, end: cursor, query } : null); setMentionIndex(0);
    };
    const chooseMention = agent => {
      if (!mentionMenu || !agent.online) return;
      const before = draft.slice(0, mentionMenu.start), insert = '@' + agent.name + ' ', after = draft.slice(mentionMenu.end);
      pendingCaret.current = before.length + insert.length;
      setDrafts(old => ({ ...old, [key]: { text: before + insert + after } }));
      setMentions(old => ({ ...old, [key]: [...(old[key] || []).filter(item => item.id !== agent.id), { id: agent.id, name: agent.name }] }));
      setRoutes(old => ({ ...old, [key]: 'auto' })); setMentionMenu(null);
    };
    const send = () => {
      if (!draft.trim() || !data || busy.current) return;
      const mentionedIds = (mentions[key] || []).filter(item => mentionPresent(draft, item.name)).map(item => item.id);
      const resolved = route === 'auto' ? resolveGroupTargets(draft, eligible, [...selectedTargets, ...mentionedIds]) : { agentIds: route === 'selected' ? selectedTargets.filter(value => eligible.some(agent => agent.id === value)) : [], error: '' };
      if (resolved.error) { setMentionMenu(null); setError(resolved.error); return; }
      if (route === 'selected' && !resolved.agentIds.length) { setError('Choose an agent or select Humans only.'); return; }
      if (resolved.agentIds.length > 8) { setError('Choose up to eight agents for one message.'); return; }
      const signature = JSON.stringify([draft, route, resolved.agentIds, options]), requestId = drafts[key]?.signature === signature ? drafts[key].requestId : uuid();
      setDrafts(old => ({ ...old, [key]: { ...old[key], text: draft, signature, requestId } }));
      act('message', { id, channelId, text: draft, routing: route, agentIds: resolved.agentIds, requestId, requestSettings: options }, (value, scope) => {
        adopt(value, scope); setDrafts(old => old[key]?.text === draft ? { ...old, [key]: { text: '' } } : old); setMentions(old => ({ ...old, [key]: [] })); setMentionMenu(null);
      });
    };
    const startChannel = item => {
      openView('Groups');
      setChannelName(item?.name || '');
      setChannelProject(item?.projectId || '');
      setEditingChannel(item?.id || '');
      setMode('channel');
    };
    const startProject = item => {
      openView('Projects');
      setProjectName(item?.name || '');
      setEditingProject(item?.id || '');
      setMode('project');
    };
    const projectOptions = [h('option', { key: '', value: '' }, 'No shared project'), ...projects.map(project => h('option', { key: project.id, value: project.id }, project.name))];
    const saveChannel = event => { event.preventDefault(); act('channel-save', { id, name: channelName.trim(), ...(editingChannel ? { channelId: editingChannel } : {}), projectId: channelProject || null }, (value, scope) => { adopt(value, scope, true); setMode(''); }); };
    const saveProject = event => { event.preventDefault(); act('project-save', { id, channelId, name: projectName.trim(), ...(editingProject ? { projectId: editingProject } : {}) }, (value, scope) => { adopt(value, scope); setMode(''); }); };
    const bindProject = (projectId, localId) => act('project-bind', { id, channelId, projectId, localProjectId: localId || null }, (value, scope) => {
      if (scope.id !== current.current.id) return;
      setSharing(old => ({ ...localDefaults(old), projectBindings: value.sharing?.projectBindings || {} }));
      adopt(value, scope); setNotice(value.notice || 'Project connection saved on this PC.');
    });
    const mapping = project => field('Local project for ' + project.name, h('select', { 'aria-label': 'Local project for ' + project.name, value: sharing?.projectBindings?.[project.id] || '', onChange: event => bindProject(project.id, event.target.value) },
      h('option', { value: '' }, 'Not connected to this PC'), localProjects.map(local => h('option', { key: local.id, value: local.id, disabled: local.folderAvailable === false }, local.name + (local.folderAvailable === false ? ' · folder unavailable' : '')))), 'This connection stays on this PC.');
    const deleteChannel = () => {
      if (!channel || channel.private || channel.id === 'general') return;
      setChannelMenu(false); setConfirmAction('channel');
    };
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

    const inSettings = settingsPages.some(([page]) => page === tab);
    const browsingGroups = tab === 'Groups' && mode !== 'channel';
    const activeGroupName = data?.group?.name || selectedGroup?.name;
    const groupStatus = group => group.hosting ? 'Hosted on this PC' : group.connected ? 'Connected to host' : 'Reconnecting to host…';
    const groupAvatar = name => h('span', { className: 'gw-group-icon', 'aria-hidden': true }, name?.slice(0, 1).toUpperCase() || 'G');
    const chooseGroup = group => select(group.id, saved.current.channels?.[group.id] || 'general');
    const navRow = (label, glyph, action, { active, key, extra } = {}) => button(
      h(React.Fragment, null, glyph, h('span', { className: 'gw-nav-label' }, label), extra), action,
      { key, className: 'gw-nav-row', 'aria-label': label, 'aria-current': active ? 'page' : undefined, title: label }
    );
    const channelRow = item => navRow(item.name, navIcon(item.private ? 'direct' : 'channel'), () => openChannel(item.id), {
      key: item.id, active: tab === 'Chat' && item.id === channelId,
    });
    const directChannels = channels.filter(item => item.private);
    const sidebar = h('aside', {
      className: 'gw-sidebar', id: navigationId, 'aria-label': 'Group navigation',
      onKeyDown: event => { if (navigationOpen && event.key === 'Escape') setNavigationOpen(false); },
    },
    button(h(React.Fragment, null, navIcon('back'), 'Back to Chat Core'), () => setNavigationOpen(false), {
      className: 'gw-mobile-back', ref: closeNavigationButton,
    }),
    h('div', { className: 'gw-section-heading' }, h('h3', null, 'Groups')),
    h('nav', { className: 'gw-rail-list', 'aria-label': 'Groups' },
      navRow('All groups', navIcon('groups'), () => openView('Groups'), { active: browsingGroups }),
      groups.map(group => navRow(group.name, groupAvatar(group.name), () => chooseGroup(group), {
        key: group.id, active: group.id === id && !browsingGroups,
      }))),
    id ? h(React.Fragment, null,
      h('div', { className: 'gw-section-heading' }, h('h3', null, 'Channels'),
        button(navIcon('plus'), () => startChannel(), { 'aria-label': 'Create channel', title: 'Create channel', disabled: pending || !data })),
      h('nav', { className: 'gw-rail-list', 'aria-label': 'Channels' }, channels.filter(item => !item.private).map(channelRow)),
      h('div', { className: 'gw-section-heading' }, h('h3', null, 'Direct chats'),
        directChannels.length ? button(navIcon('plus'), () => openView('Agents'), {
          'aria-label': 'Start a direct chat', title: 'Start a direct chat',
        }) : null),
      h('nav', { className: 'gw-rail-list', 'aria-label': 'Direct chats' },
        directChannels.length ? directChannels.map(channelRow) :
          navRow('Start a direct chat', navIcon('direct'), () => openView('Agents'))),
      h('div', { className: 'gw-section-heading gw-workspace-heading' }, h('h3', null, 'Workspace')),
      h('nav', { className: 'gw-rail-list', 'aria-label': 'Workspace sections' },
        navRow('People & agents', navIcon('people'), () => openView('Agents'), { active: tab === 'Agents' || tab === 'Team builder' }),
        navRow('Tasks', navIcon('tasks'), () => openView('Plan'), { active: tab === 'Plan', extra: activeTasks.length ? badge(activeTasks.length) : null }),
        navRow('Projects', navIcon('projects'), () => openView('Projects'), { active: tab === 'Projects' }),
        navRow('Settings', navIcon('settings'), () => openView('Advanced'), { active: inSettings }))) : null);

    const cancelActions = activeTasks.filter(task => owner || task.requestedBy === data?.self?.id || agents.find(agent => agent.id === task.agentId)?.memberId === data?.self?.id).map(task => h('div', { className: 'gw-cancel-row', key: task.id }, h('span', null, h('strong', null, task.agentName || 'Agent'), h('small', null, status(task.status))), button('Cancel task', () => act('cancel', { id, channelId, taskId: task.id }, (value, scope) => adopt(value, scope)), { disabled: pending, 'aria-label': 'Cancel task for ' + (task.agentName || 'Agent') })));
    const attributed = task => { const pc = display.showPCNames && agents.find(agent => agent.id === task.agentId)?.pcName; return pc ? { ...task, agentName: (task.agentName || 'Agent') + ' · ' + pc } : task; };
    const activity = h('section', { className: 'gw-activity', 'aria-label': 'Channel tasks' },
      h('div', { className: 'gw-page-heading' },
        h('div', null, h('h3', null, 'Tasks'), h('p', null, 'Work and progress in ' + (channel?.name || 'this channel') + '.')),
        button('Back to chat', () => openView('Chat'))),
      h(Board, { data: { ...data, tasks: list(data?.tasks).map(attributed), plan: list(data?.plan).map(item => {
        const task = list(data?.tasks).find(task => task.id === item.taskId);
        return task ? { ...item, agentName: attributed(task).agentName } : item;
      }) } }),
      cancelActions.length ? h('section', { className: 'gw-cancel-actions', 'aria-label': 'Task controls' }, cancelActions) : null);
    const routingSettings = h(React.Fragment, null,
      field('Message routing', h('select', { 'aria-label': 'Message routing', value: route,
        onChange: event => setRoutes(old => ({ ...old, [key]: event.target.value })) },
        h('option', { value: 'auto' }, 'Follow agent rules'),
        h('option', { value: 'selected' }, 'Selected agents only'),
        h('option', { value: 'humans-only' }, 'Humans only'))),
      agents.length && route !== 'humans-only' ? h('fieldset', { className: 'generation-group-targets' },
        h('legend', null, 'Choose agents'), agents.map(agent => h('label', { key: agent.id },
          h('input', { type: 'checkbox', checked: selectedTargets.includes(agent.id),
            disabled: !allowed(agent) || !agent.online, 'aria-label': 'Ask ' + agent.name + ' on ' + agent.pcName,
            onChange: event => {
              const checked = event.target.checked;
              setTargets(old => ({ ...old, [key]: checked ? [...new Set([...(old[key] || []), agent.id])].slice(0, 8) :
                (old[key] || []).filter(value => value !== agent.id) }));
            } }),
          h('span', null, '@' + agent.name, h('small', null, agent.pcName + ' · ' + (!allowed(agent) ? 'Restricted here' :
            agent.online ? agent.modelLabel || agent.modelId : 'Offline')))))) : null);
    const mentionSuggestions = mentionMenu ? h('div', { className: 'composer-skill-menu', role: 'listbox', 'aria-label': 'Mention an agent' },
      mentionCandidates.length ? mentionCandidates.map((agent, index) => button(
        h(React.Fragment, null, h('strong', null, '@' + agent.name),
          h('small', null, agent.pcName + ' · ' + (agent.online ? agent.modelLabel || agent.modelId : 'Offline'))),
        () => chooseMention(agent), { key: agent.id, role: 'option', 'aria-selected': index === mentionIndex,
          disabled: !agent.online, onMouseDown: event => event.preventDefault() })) : h('small', null, 'No matching agents')) : null;
    const mentionFromMenu = () => {
      const at = composerRef.current?.selectionStart ?? draft.length;
      const before = draft.slice(0, at), inserted = (before && !/\s$/.test(before) ? ' ' : '') + '@';
      pendingCaret.current = before.length + inserted.length;
      changeDraft(before + inserted + draft.slice(at), pendingCaret.current);
    };
    const handleMentionKey = event => {
      if (!mentionMenu) return false;
      if (event.key === 'Escape') {
        setMentionMenu(null);
      } else if (mentionCandidates.length && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
        setMentionIndex(old => (old + (event.key === 'ArrowDown' ? 1 : -1) + mentionCandidates.length) % mentionCandidates.length);
      } else if (mentionCandidates[mentionIndex]?.online && (event.key === 'Tab' || event.key === 'Enter' && !event.shiftKey)) {
        chooseMention(mentionCandidates[mentionIndex]);
      } else return false;
      event.preventDefault();
      return true;
    };
    const thread = h('div', { className: 'aui-thread-viewport gw-messages', ref: messagesRef, role: 'log',
      'aria-label': 'Group messages', 'aria-live': 'polite' },
      channel?.private ? h('p', { className: 'gw-private-note' }, 'Private to this channel’s participants. Messages are stored on the group host; agent tasks are also stored on the agent owner’s PC.') : null,
      list(data?.messages).length ? data.messages.map(message => h('article', { key: message.id, className: 'gw-message ' + (message.kind || '') },
        h('div', { className: 'gw-avatar', 'aria-hidden': true }, message.kind === 'agent' ? '✦' : (message.authorName || 'G').slice(0, 1)), h('div', { className: 'gw-message-body' }, h('header', null, h('strong', null, message.authorName || 'Group'), message.kind === 'agent' ? badge(display.showPCNames ? agents.find(agent => agent.id === message.agentId)?.pcName || 'Agent' : 'Agent') : null, display.showTimestamps ? h('time', { dateTime: message.createdAt }, Number.isFinite(Date.parse(message.createdAt)) ? new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '') : null), h('div', { className: 'gw-message-text' }, message.text),
          /opencore-group-local:\/\/conversation\/([^\s]+)/.test(message.text) ? button('Open complete result on this PC', async () => { try { const match = message.text.match(/opencore-group-local:\/\/conversation\/([^\s]+)/); setLocalResult(await worker?.readResult(id, decodeURIComponent(match[1])) || 'The complete result is on the agent owner’s PC.'); } catch (failure) { setError(String(failure)); } }) : null)))
        : h('div', { className: 'empty-state large chat-empty' },
          h('img', { src: '/opencore-logo.png', alt: '' }),
          h('h3', null, channel?.private ? channel.name : 'Welcome to #' + (channel?.name || 'General')),
          h('p', null, channel?.private ? 'Send a message to start this direct chat.' : 'Send a message or type @ to mention an agent.'),
          !channel?.private && !agents.length ? button('Set up an agent', () => openView('Share my PC')) : null));
    const renderChat = actions => h(Conversation, {
      key, entries: [], defaultSkills: [], runtimeSnapshot: { status: 'stopped' }, selectedProfile: 'stopped',
      title: activeGroupName || 'Group', client: selectedGroup ? groupStatus(selectedGroup) : 'Chat Core',
      telemetry: { completionTokens: 0 }, onNotice: setError, inferenceBlocked: pending ? 'Sending…' : undefined,
      groupChat: {
        actions, thread, messageCount: list(data?.messages).length, draft, inputRef: composerRef,
        effort: options.effort, approval: options.approval,
        placeholder: channel?.private ? 'Message this private chat…' : 'Message #' + (channel?.name || 'General') + '…',
        onDraftChange: changeDraft, onSend: send, onEffort: value => setOption('effort', value),
        onApproval: value => setOption('approval', value), onKeyDown: handleMentionKey, onMention: mentionFromMenu,
        settings: routingSettings, suggestions: mentionSuggestions,
      },
    });

    const people = h('div', { className: 'gw-people' }, h('div', { className: 'gw-page-heading' },
      h('div', null, h('h3', null, 'People & agents'), h('p', null, 'Start a direct chat or choose agents for a task.')),
      h('div', { className: 'gw-actions' }, button('Build a team', () => openView('Team builder')),
        button('Set up an agent', () => openView('Share my PC')))),
      h('div', { className: 'gw-agent-grid' }, agents.map(agent => h('article', { className: 'gw-person-card', key: agent.id }, h('header', null, h('span', { className: 'gw-avatar agent', 'aria-hidden': true }, '✦'), h('div', null, h('h3', null, '@' + agent.name), h('small', null, agent.modelLabel || agent.modelId)), badge(agent.online ? 'Online' : 'Offline', agent.online ? 'online' : '')),
        h('p', null, agent.pcName), h('small', null, ({ mentions: 'Replies to mentions', always: 'Replies to every human message', manual: 'Replies when selected' }[agent.replyMode] || 'Replies to mentions') + (agent.aliases?.length ? ' · Also @' + agent.aliases.join(', @') : '')),
        agent.allowedMembers?.length || agent.allowedChannels?.length ? h('small', null, 'Access is restricted by this agent’s owner.') : null,
        h('div', { className: 'gw-actions' }, button('Ask agent', () => askAgent(agent), { disabled: !agent.online || !allowed(agent), 'aria-label': 'Ask agent ' + agent.name + ' on ' + agent.pcName }), button('Direct chat', () => openDirect([], [agent.id]), { disabled: pending, 'aria-label': 'Direct chat with ' + agent.name + ' on ' + agent.pcName }))))),
      !agents.length ? h('p', { className: 'gw-empty' }, 'No agents have been added to this group yet.') : null,
      h('h3', null, 'Members'), h('div', { className: 'gw-member-list' }, members.map(member => h('article', { className: 'gw-member', key: member.id }, h('span', { className: 'gw-avatar', 'aria-hidden': true }, member.name?.slice(0, 1)), h('div', null, h('strong', null, member.name + (member.id === data?.self?.id ? ' (you)' : '')), h('small', null, member.pcName + ' · ' + (member.online ? 'Online' : 'Offline'))), member.id !== data?.self?.id ? h('div', { className: 'gw-actions' }, button('Direct chat', () => openDirect([member.id], []), { disabled: pending, 'aria-label': 'Direct chat with ' + member.name }), owner ? button('Remove', () => { if (confirm('Remove ' + member.name + ' from this group?')) act('revoke', { id, memberId: member.id }, (value, scope) => adopt(value, scope)); }, { disabled: pending, 'aria-label': 'Remove ' + member.name + ' from group' }) : null) : badge(member.role === 'owner' ? 'Host' : 'Member')))));

    const restrict = (agent, label, property, options) => h('fieldset', { className: 'gw-restrictions' }, h('legend', null, label), h('small', null, 'No checked items means everyone or every available channel.'), options.map(option => h('label', { key: option.id }, h('input', { type: 'checkbox', checked: list(agent[property]).includes(option.id), 'aria-label': label + ' ' + option.name + ' for ' + agent.name, onChange: event => editAgent(agent.key, { [property]: event.target.checked ? [...new Set([...list(agent[property]), option.id])] : list(agent[property]).filter(value => value !== option.id) }) }), option.name)));
    const advancedAgent = agent => {
      const toggle = (label, property) => h('label', { className: 'gw-check', key: property }, h('input', { type: 'checkbox', 'aria-label': label + ' for ' + agent.name, checked: agent[property] !== false, onChange: event => editAgent(agent.key, { [property]: event.target.checked }) }), label);
      const number = (label, property, maximum, fallback) => field(label, input(label + ' for ' + agent.name, agent[property] ?? fallback, value => editAgent(agent.key, { [property]: Math.max(property === 'maxSubagents' ? 1 : 0, Math.min(maximum, Math.floor(Number(value) || 0))) }), { type: 'number', min: property === 'maxSubagents' ? 1 : 0, max: maximum }));
      return h('details', { className: 'gw-agent-advanced' }, h('summary', null, 'Advanced settings'), h('div', { className: 'gw-form-grid' },
        toggle('Enable this role', 'enabled'),
        field('Reasoning effort', h('select', { 'aria-label': 'Reasoning effort for ' + agent.name, value: agent.reasoningEffort === 'ultra' ? 'max' : agent.reasoningEffort || 'medium', onChange: event => editAgent(agent.key, { reasoningEffort: event.target.value }) }, efforts.map(([value,label]) => h('option', { key: value, value }, label)))),
        field('Role tool approval', h('select', { 'aria-label': 'Role tool approval for ' + agent.name, value: agent.approvalMode || 'inherit', onChange: event => editAgent(agent.key, { approvalMode: event.target.value }) }, h('option', { value: 'inherit' }, 'Use this PC’s group policy'), h('option', { value: 'ask-every-time' }, 'Ask for every tool action'), h('option', { value: 'approve-for-me' }, 'Approve for me'), h('option', { value: 'allow-chat' }, 'Allow tools for this role’s tasks'), h('option', { value: 'allow-all' }, 'Allow all enabled tools'))),
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
    const sharingPanel = sharing ? h('form', { className: 'gw-sharing', onSubmit: saveSharing }, h('div', { className: 'gw-page-heading' },
      h('div', null, h('h3', null, 'Agents on this PC'), h('p', null, 'Choose a model and give it a role in the group.')),
      button('Refresh models', loadModels, pendingButton)),
      h('label', { className: 'gw-sharing-toggle' }, h('input', { type: 'checkbox', 'aria-label': 'Share models from this PC', checked: !!sharing.enabled, onChange: event => editSharing({ enabled: event.target.checked }) }), h('span', null, h('strong', null, 'Share models from this PC'), h('small', null, sharing.enabled ? 'Enabled for the agents you save below.' : 'Off. Group members cannot start an agent on this PC.'))),
      modelError ? h('p', { className: 'gw-error' }, modelError) : null,
      h('div', { className: 'gw-add-agent' }, field('Model for new agent', h('select', { 'aria-label': 'Model for new agent', value: addModel, onChange: event => setAddModel(event.target.value) }, !models.length ? h('option', { value: '' }, 'No supported text models available') : null, models.map(model => h('option', { key: model.id, value: model.id }, modelOption(model))))), button('Add agent', () => addAgent(models.find(model => model.id === addModel)), { disabled: !addModel || configured.length >= 8 })),
      h('section', { className: 'gw-role-templates', 'aria-label': 'Quick role templates' }, h('small', null, 'Start with a role, then change its name, model or instructions.'), h('div', { className: 'gw-actions' }, Object.keys(roleTemplates).map(role => button(role, () => addAgent(models.find(model => model.id === addModel), role), { key: role, disabled: !addModel || configured.length >= 8, 'aria-label': 'Add ' + role + ' role' })))),
      h('details', { className: 'gw-quick-models' }, h('summary', null, 'Quick selection · ready models'), models.filter(ready).map(model => h('label', { key: model.id }, h('input', { type: 'checkbox', 'aria-label': 'Share ' + model.label, checked: configured.some(agent => agent.id === model.id), onChange: event => event.target.checked ? addAgent(model) : editSharing({ models: configured.filter(agent => agent.id !== model.id) }) }), model.label, badge(isAPI(model) ? 'API' : 'Local')))),
      configured.some(agent => isAPI(models.find(model => model.id === agent.id) || agent)) ? h('small', null, 'API agents send shared task context to their configured provider. API credentials stay on this PC.') : null,
      !models.length ? h('p', { className: 'gw-empty' }, 'Refresh models to find supported local models. Optional API profiles can be configured in OpenCore.') : null,
      h('div', { className: 'gw-agent-editors' }, agentEditors),
      h('section', { className: 'gw-permissions' }, h('h3', null, 'Tool access on this PC'), h('p', null, 'Tools run under your selected policy on the executing PC. Another group member cannot change this setting.'),
        h('div', { className: 'gw-form-grid' }, field('Tools requested by group agents', h('select', { 'aria-label': 'Group tool approval', value: sharing.approvalMode, onChange: event => editSharing({ approvalMode: event.target.value }) }, h('option', { value: 'ask-every-time' }, 'Ask me before every tool action'), h('option', { value: 'allow-chat' }, 'Allow tools for these group tasks'), h('option', { value: 'allow-all' }, 'Allow all tools for shared agents'))), field('Maximum local subagents', input('Maximum local subagents', sharing.maxSubagents, value => editSharing({ maxSubagents: Math.max(1, Math.min(8, Math.floor(Number(value) || 1))) }), { type: 'number', min: 1, max: 8 }))),
        sharing.approvalMode !== 'ask-every-time' ? h('p', { className: 'gw-permission-note' }, 'These agents may use this PC’s enabled tools without asking for every action. The selected policy applies only after you save it.') : null),
      projects.length ? h('section', null, h('h3', null, 'Shared project → your local project'), projectMappings) : null,
      h('div', { className: 'gw-save-bar' }, h('small', null, dirty ? 'You have unsaved changes.' : 'Only this PC’s sharing settings appear here.'), button('Save sharing', null, { type: 'submit', disabled: pending || !dirty || !validSharing })), !validSharing ? h('p', { className: 'gw-error' }, 'Name each role and choose a ready model, or allow its installation and preparation before the first task.') : null) : h('p', { className: 'gw-empty' }, 'Loading this PC’s sharing settings…');
    const teamPanel = h('section', { className: 'gw-teams' }, h('div', { className: 'gw-page-heading' },
      h('div', null, h('h3', null, 'Team builder'), h('p', null, 'Select agents for your next message. Each agent runs on the PC shown below.')),
      h('div', { className: 'gw-actions' }, button('Back to people & agents', () => openView('Agents')),
        button('Add roles on my PC', () => openView('Share my PC')))),
      h('p', { className: 'gw-muted' }, 'Choose shared agents for the next task in this channel. Each PC owner controls their models and tool permissions. Local models are primary; API profiles are optional.'),
      h('div', { className: 'gw-pc-grid' }, members.map(member => { const roles = agents.filter(agent => agent.memberId === member.id); return h('article', { key: member.id, className: 'gw-pc-card' }, h('header', null, h('div', null, h('h3', null, member.pcName), h('small', null, member.name + ' · ' + roles.length + (roles.length === 1 ? ' role' : ' roles'))), badge(member.online ? 'Online' : 'Offline', member.online ? 'online' : '')),
        roles.length ? roles.map(agent => h('label', { key: agent.id, className: 'gw-team-role' }, h('input', { type: 'checkbox', 'aria-label': 'Use ' + agent.name + ' on ' + agent.pcName + ' in task team', checked: selectedTargets.includes(agent.id), disabled: !allowed(agent) || !agent.online, onChange: event => { const checked = event.target.checked; setTargets(old => ({ ...old, [key]: checked ? [...new Set([...(old[key] || []), agent.id])].slice(0, 8) : (old[key] || []).filter(value => value !== agent.id) })); } }), h('span', null, h('strong', null, agent.name), h('small', null, agent.modelLabel || agent.modelId), h('small', null, agent.enabled === false ? 'Disabled by owner' : !allowed(agent) ? 'Restricted in this channel' : agent.replyMode === 'manual' ? 'Explicit selection' : agent.replyMode === 'always' ? 'Every human message' : 'Mentions')))) : h('p', { className: 'gw-muted' }, member.id === data?.self?.id ? 'Set up an agent on this PC.' : 'This member has not shared a role yet.'), member.id === data?.self?.id ? button('Configure this PC’s roles', () => setTab('Share my PC')) : null); })),
      h('div', { className: 'gw-save-bar' }, h('span', null, selectedTargets.length + ' selected roles · ' + new Set(agents.filter(agent => selectedTargets.includes(agent.id)).map(agent => agent.memberId)).size + ' PCs'), button('Compose for this team', () => { setRoutes(old => ({ ...old, [key]: 'selected' })); setTab('Chat'); }, { disabled: !selectedTargets.length })));
    const preferences = h('section', { className: 'gw-settings-card', 'aria-label': 'Display preferences' },
      h('header', null, h('h3', null, 'Display'), h('small', null, 'Applies on this device.')),
      h('label', { className: 'gw-setting-row' }, h('span', null, 'Message density'),
        h('select', { 'aria-label': 'Message density', value: display.density,
          onChange: event => setDisplay(old => ({ ...old, density: event.target.value })),
        }, h('option', { value: 'comfortable' }, 'Comfortable'), h('option', { value: 'compact' }, 'Compact'))),
      h('label', { className: 'gw-setting-row' }, h('span', null, 'Message timestamps'),
        h('input', { type: 'checkbox', 'aria-label': 'Show message timestamps', checked: display.showTimestamps,
          onChange: event => setDisplay(old => ({ ...old, showTimestamps: event.target.checked })) })),
      h('label', { className: 'gw-setting-row' }, h('span', null, 'PC names'),
        h('input', { type: 'checkbox', 'aria-label': 'Show PC names in messages and activity', checked: display.showPCNames,
          onChange: event => setDisplay(old => ({ ...old, showPCNames: event.target.checked })) })));
    const membership = h('section', { className: 'gw-settings-card', 'aria-label': 'Group membership' },
      h('header', null, h('h3', null, 'Membership'), h('small', null, owner ? 'You host this group.' : 'You belong to this group.')),
      h('div', { className: 'gw-setting-row gw-membership-row' },
        h('div', null, h('strong', null, owner ? 'Delete this group' : 'Leave this group'),
          h('small', null, owner ? 'Removes the group and its shared history for everyone.' : 'Removes your access and shared agents.')),
        button(owner ? 'Delete group' : 'Leave group', () => setConfirmAction(owner ? 'group' : 'leave'), {
          className: 'gw-danger-button', disabled: pending,
        })));
    const settings = h('section', { className: 'gw-group-settings', 'aria-label': 'Group settings' },
      h('h2', null, 'Group settings'),
      h('nav', { className: 'gw-settings-sections', 'aria-label': 'Group settings sections' },
        settingsPages.map(([page, label]) => button(label, () => openView(page), {
          key: page, 'aria-current': tab === page ? 'page' : undefined,
        }))),
      tab === 'Membership' ? membership : tab === 'Share my PC' ? sharingPanel : preferences);
    const projectPanel = h('section', { className: 'gw-projects' }, h('div', { className: 'gw-page-heading' }, h('div', null, h('h3', null, 'Shared projects'), h('p', null, 'Organize channels around a project. Every PC owner maps the name to their own existing local project.')), owner ? button('Create project', () => startProject()) : null),
      mode === 'project' ? editor : null, !projects.length ? h('p', { className: 'gw-empty' }, owner ? 'Create a named project, then connect a channel to it.' : 'The group owner can create shared project names.') : projects.map(project => h('article', { className: 'gw-project-card', key: project.id }, h('header', null, h('h3', null, project.name), owner ? button('Rename', () => startProject(project), { 'aria-label': 'Rename project ' + project.name }) : null), h('small', null, channels.filter(item => item.projectId === project.id).length + ' linked channels'), mapping(project))),
      dirty ? h('div', { className: 'gw-save-bar' }, h('small', null, 'Save this PC’s project mappings.'), button('Save sharing', saveSharing, { disabled: pending || !validSharing })) : null);

    const directory = h('section', { className: 'gw-group-directory', 'aria-label': 'Group directory' }, editor ||
      h(React.Fragment, null,
        h('div', { className: 'gw-actions' },
          button('Create group', () => setMode('create'), { className: 'gw-primary' }),
          button('Join with invite', () => setMode('join'))),
        groups.length ? h('div', { className: 'gw-directory-list' },
          groups.map(group => button(h(React.Fragment, null,
            groupAvatar(group.name), h('span', null, h('strong', null, group.name), h('small', null, groupStatus(group)))),
          () => chooseGroup(group), { key: group.id, className: 'gw-directory-row' }))) :
          h('p', { className: 'gw-muted' }, 'Create a group to start a conversation, or join with an invite.')));
    const showGroup = id && !browsingGroups;
    const groupHeader = h('header', { className: 'gw-main-header' },
      button(h(React.Fragment, null, navIcon('browse'), 'Browse'), () => setNavigationOpen(true), {
        className: 'gw-browse-button', ref: browseButton, 'aria-label': 'Browse groups and channels',
        'aria-controls': navigationId, 'aria-expanded': navigationOpen,
      }),
      h('div', { className: 'gw-group-identity' },
        showGroup ? groupAvatar(activeGroupName) : null,
        h('div', null, h('h2', { title: showGroup ? activeGroupName : undefined }, showGroup ? activeGroupName || 'Group' : 'Your groups'),
          showGroup && selectedGroup ? h('small', null, groupStatus(selectedGroup)) : null)),
      showGroup && owner ? h('div', { className: 'group-header-actions' },
        button('Invite people', () => act('invite', { id }, value => setInvite(value)), { ...pendingButton, className: 'gw-primary' })) : null);
    const canEditChannel = !!(channel && !channel.private &&
      (owner || (data?.self?.id && channel.createdBy === data.self.id)));
    const channelActions = h(React.Fragment, null,
      button(navIcon('browse'), () => setNavigationOpen(true), {
        className: 'gw-browse-button', ref: browseButton, 'aria-label': 'Browse groups and channels',
        'aria-controls': navigationId, 'aria-expanded': navigationOpen,
      }),
      h('select', { className: 'gw-channel-select', 'aria-label': 'Current channel', value: channelId,
        onChange: event => openChannel(event.target.value) },
        channels.map(item => h('option', { key: item.id, value: item.id }, item.private ? 'Private · ' + item.name : '# ' + item.name))),
      owner ? button(navIcon('people'), () => act('invite', { id }, value => setInvite(value)), {
        ...pendingButton, 'aria-label': 'Invite people', title: 'Invite people',
      }) : null,
      selectedProject ? button(h(React.Fragment, null, navIcon('projects'), h('span', null, selectedProject.name,
        sharing?.projectBindings?.[selectedProject.id] ? ' · Linked' : ' · Connect PC')),
      () => openView('Projects'), { className: 'gw-project-link', title: 'Connect this shared project to a folder on this PC' }) : null,
      activeTasks.length ? button('Tasks · ' + activeTasks.length, () => openView('Plan'), {
        className: 'gw-running-tasks', 'aria-label': 'View running tasks',
      }) : null,
      canEditChannel ? h('div', { className: 'gw-channel-menu' },
        button('⋯', () => setChannelMenu(open => !open), {
          'aria-label': 'Channel actions', 'aria-expanded': channelMenu, title: 'Channel actions',
        }),
        channelMenu ? h('div', { className: 'gw-channel-menu-items' },
          owner ? button('Invite to channel', () => { setChannelMenu(false); act('invite', { id, channelId }, value => setInvite(value)); }, pendingButton) : null,
          button('Edit channel', () => startChannel(channel), { 'aria-label': 'Edit current channel', disabled: pending }),
          channel.id !== 'general' ? button('Delete channel', deleteChannel, {
            'aria-label': 'Delete current channel', className: 'gw-danger-button', disabled: pending,
          }) : null) : null) : null);
    const chatOpen = tab === 'Chat' && !!data;
    const views = { Chat: chatOpen ? renderChat(channelActions) : null, Agents: people, 'Team builder': teamPanel, Plan: activity, Projects: projectPanel };
    const content = tab === 'Groups' ? directory :
      !data ? h('p', { className: 'gw-empty', role: 'status' }, 'Loading workspace…') :
      inSettings ? settings : views[tab];

    return h(embedded && EmbeddedWorkspace ? EmbeddedWorkspace : Dialog, { label: 'Chat Core', className: 'group-workspace gw-density-' + display.density, close },
      !embedded ? h('header', { className: 'gw-header' }, h('div', { className: 'gw-brand' }, h('img', { src: '/opencore-logo.png', alt: 'OpenCore', width: 36, height: 36 }), h('h1', null, 'Chat Core')), button('×', close, { className: 'gw-close', 'aria-label': 'Close Chat Core' })) : null,
      h('div', { className: 'gw-shell', 'data-navigation-open': navigationOpen },
        h('div', { className: 'gw-navigation-rail' }, sidebar),
        h('main', { className: 'gw-main' }, chatOpen ? null : groupHeader,
        (error || refreshError) ? h('p', { className: 'gw-banner gw-error', role: 'alert' }, error || refreshError) : null, notice ? h('p', { className: 'gw-banner', role: 'status' }, notice) : null,
        confirmAction ? h('section',{className:'gw-confirm',role:'alertdialog','aria-label':confirmAction==='channel'?'Delete channel':confirmAction==='group'?'Delete group':'Leave group'},
          h('strong',null,confirmAction==='channel'?'Delete #'+(channel?.name||'channel')+' for everyone?':confirmAction==='group'?'Delete this group for everyone?':'Leave this group?'),
          h('p',null,confirmAction==='channel'?'Its messages and completed activity will be permanently deleted.':confirmAction==='group'?'All shared channels, messages and members will be permanently removed from this host.':'Your access and shared agents will be removed from the group.'),
          h('div',{className:'gw-actions'},button('Cancel',()=>setConfirmAction('')),button(confirmAction==='channel'?'Delete channel':confirmAction==='group'?'Delete group':'Leave group',()=>{const action=confirmAction;setConfirmAction('');if(action==='channel')act('channel-delete',{id,channelId},(value,scope)=>{adopt(value,scope,true);setNotice('Channel deleted.');});else act(action==='group'?'delete-group':'leave',{id},()=>{setGroups(old=>old.filter(group=>group.id!==id));select('');setNotice(action==='group'?'Group deleted.':'You left the group.');});},{disabled:pending,'aria-label':confirmAction==='channel'?'Confirm delete channel':confirmAction==='group'?'Confirm delete group':'Confirm leave group'}))) : null,
        workerState.task ? h('p', { className: 'gw-banner' }, 'This PC is working · ' + (workerState.task.agentName || workerState.task.modelId || 'Group agent')) : null, workerState.error ? h('p', { className: 'gw-banner gw-error' }, workerState.error) : null,
        invite ? h('section', { className: 'gw-invite' }, field('Group invite', input('Group invite', invite.invite, () => {}, { readOnly: true })), button('Copy invite', async () => { try { await navigator.clipboard.writeText(invite.invite); setNotice('Invite copied.'); } catch { setNotice('Select and copy the invite above.'); } }), button('Dismiss invite', () => setInvite(null)), h('small', null, 'One-time invite · expires in ' + Math.ceil((invite.expiresIn || 300) / 60) + ' minutes. Your friend must be able to reach this group’s host.')) : null,
        h('div', { className: 'gw-content gw-content-' + tab.toLowerCase().replace(/\s/g, '-') }, content))),
      localResult !== null ? h(Dialog, { label: 'Complete local group result', className: 'gw-local-result', close: () => setLocalResult(null) }, h('header', { className: 'gw-header' }, h('h2', null, 'Complete result on this PC'), button('Close', () => setLocalResult(null))), h('pre', null, localResult)) : null);
  }

  function PhoneGroupPanel(props) {
    const [capability, setCapability] = React.useState(null);
    const reason = 'Chat Core needs the paired PC service. Choose Paired PC in Execution device settings to use your PC’s groups.';
    React.useEffect(() => {
      let live = true;
      const check = async () => {
        try {
          const value = await service('capabilities', {});
          if (live) setCapability(value?.available === true ? { available: true } : {
            available: false, reason: typeof value?.reason === 'string' && value.reason.trim() ? value.reason : reason,
          });
        } catch {
          if (live) setCapability({ available: false, reason });
        }
      };
      check();
      return () => { live = false; };
    }, []);
    if (capability?.available) return h(WorkspacePanel, props);
    return h(props.embedded && EmbeddedWorkspace ? EmbeddedWorkspace : Dialog, { label: 'Chat Core', className: 'group-workspace', close: props.close },
      !props.embedded ? h('header', { className: 'gw-header' }, h('div', { className: 'gw-brand' }, h('img', { src: '/opencore-logo.png', alt: 'OpenCore', width: 36, height: 36 }), h('div', null, h('h1', null, 'Chat Core'), h('p', null, 'People, local models and shared roles'))), button('×', props.close, { className: 'gw-close', 'aria-label': 'Close Chat Core' })) : null,
      h('main', { className: 'gw-main' }, h('div', { className: 'gw-content' }, h('section', { className: 'gw-overview' },
        h('h2', null, capability ? 'Chat Core runs on your PC' : 'Checking Chat Core availability…'),
        h('p', { role: 'status' }, capability ? capability.reason : 'Checking the group service for this execution device…'),
        capability ? h('div', { className: 'gw-actions' }, button('Choose Phone or Paired PC', () => globalThis.__opencoreExecutionSettings?.())) : null))));
  }

  return function GroupPanel(props) {
    return h(globalThis.__OPENCORE_PHONE__ === true ? PhoneGroupPanel : WorkspacePanel, props);
  };
}
