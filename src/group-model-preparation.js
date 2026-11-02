// Only owner-selected local catalog records can become preparation targets.
export async function prepareGroupModel(config, deps, progress = () => {}, cancelled = async () => false) {
  if (config?.prepareIfMissing !== true) throw Error('The owner has not authorized automatic model preparation.');
  const catalogId = config.catalogId || config.id;
  if (typeof catalogId !== 'string' || typeof config.id !== 'string' || !catalogId || catalogId.length > 512)
    throw Error('Choose an exact model from this PC catalog.');
  const now = deps.now || Date.now, wait = deps.wait || (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)));
  const deadline = now() + 90 * 60 * 1000;
  let operation = null, sleeps = 0;
  const state = () => deps.installState.getSnapshot() || {};
  const operationId = snapshot => snapshot.active?.jobId ||
    (snapshot.progress?.modelId === snapshot.active?.modelId ? snapshot.progress.operationId || snapshot.progress.jobId : null);
  const ownActive = snapshot => operation?.admitted && operation.targets.has(snapshot.active?.modelId) &&
    (!operation.id || operationId(snapshot) === operation.id);
  async function cancelOwn() {
    await deps.installState.refresh(true);
    const snapshot = state();
    if (!ownActive(snapshot)) return;
    if (snapshot.active.kind === 'runtime') {
      if (snapshot.active.jobId) await deps.invoke('runtime_setup_cancel', { jobId: snapshot.active.jobId });
    } else if (snapshot.active.kind === 'download') await deps.invoke('cancel_model_install', {});
  }
  async function checkCancelled() {
    if (await cancelled()) throw Error('Group model preparation cancelled.');
    if (now() >= deadline || sleeps >= 5400) throw Error('Model preparation timed out after 90 minutes.');
  }
  async function load() {
    await deps.refresh(true);
    const library = await deps.invoke('list_model_library', {});
    if (!Array.isArray(library?.models) || !Array.isArray(deps.catalog())) throw Error('This PC model catalog is unavailable.');
    const model = library.models.find(value => value.id === catalogId);
    const entry = deps.catalog().find(value => value.id === catalogId);
    if (!model && !entry) throw Error('The owner-selected model is no longer in this PC catalog.');
    const managed = !!entry && !model?.hubEntry?.nativeProfile;
    const value = managed ? entry : model;
    const known = new Set([catalogId, model?.id, model?.operatingProfileId, model?.hubEntry?.profileId,
      entry?.profileId, ...Object.values(model?.modeProfiles || {}), ...(entry?.nativeModelIds || [])].filter(Boolean));
    if (!known.has(config.id)) throw Error('The shared profile does not match the owner-selected catalog model.');
    const text = value.supportsTextGeneration === true || value.supportsTextGeneration !== false &&
      (['text', 'brain'].includes(value.category) || model?.hubEntry?.category === 'text');
    if (!text || value.supportsTextChat === false) throw Error('This catalog choice has no conversational text runtime.');
    // hf_runtime.runtime_plan dispatches only GGUF and Transformers text packages.
    if (managed && !['gguf', 'transformers'].includes(entry.runtime?.adapter))
      throw Error('This model format needs a supported local text runtime adapter.');
    if (!managed && !model.preparedRuntime && !model.managedInstallation && !model.installation?.adapterId && model.runtimeReady === false)
      throw Error('This native model has no supported automatic runtime adapter.');
    return { model, entry, managed, value, targets: new Set([catalogId, entry?.id, model?.id, model?.operatingProfileId].filter(Boolean)) };
  }
  const installed = current => current.managed ? current.entry.status === 'installed' : current.model.installed === true;
  const ready = current => installed(current) && (current.managed ? current.entry.runtimeReady === true :
    current.model.selectable === true && current.model.runtimeReady !== false &&
      (!current.model.preparedRuntime || current.model.preparedReady === true));
  async function heartbeat(snapshot) {
    const value = snapshot.progress;
    const report = value && operation?.targets.has(value.modelId) ?
      Object.fromEntries(['modelId', 'operationId', 'phase', 'downloadedBytes', 'totalBytes', 'currentFile', 'error']
        .filter(key => value[key] !== undefined).map(key => [key, value[key]])) :
      { modelId: catalogId, phase: 'preparing', currentFile: 'Checking the selected model preparation' };
    // The worker callback also renews its task lease, including unchanged polls.
    await progress(report);
  }
  async function start(current, command, args) {
    await checkCancelled();
    await deps.installState.refresh(true);
    if (state().active) throw Error('Another model is installing. Wait for it to finish before preparing this group model.');
    operation = { targets: current.targets, command, admitted: false, id: null };
    const result = await deps.invoke(command, args);
    operation.admitted = true;
    await deps.installState.refresh(true);
    operation.id = operationId(state()) || result?.jobId || result?.id || null;
  }
  async function until(predicate) {
    for (;;) {
      await checkCancelled();
      await deps.installState.refresh(true);
      const snapshot = state();
      await heartbeat(snapshot);
      await checkCancelled();
      const current = await load();
      const matching = operation.targets.has(snapshot.progress?.modelId) &&
        (!operation.id || !snapshot.progress?.operationId || snapshot.progress.operationId === operation.id);
      const status = matching ? snapshot.progress.phase : null;
      if (['failed', 'paused', 'cancelled', 'canceled'].includes(status))
        throw Error(snapshot.progress.error || 'Model preparation ' + status + '.');
      if (predicate(current) && !ownActive(snapshot)) { operation = null; return current; }
      if (['ready', 'completed'].includes(status) && !ownActive(snapshot))
        throw Error('Model preparation ended without verified downloaded weights and generation readiness.');
      await wait(1000); sleeps++;
    }
  }
  try {
    await checkCancelled();
    let current = await load();
    if (current.managed && deps.modes?.ensure) {
      await deps.modes.ensure(current.entry);
      current = await load();
    }
    if (!installed(current)) {
      if (current.value.installable !== true) throw Error('This catalog package cannot be installed automatically.');
      await start(current, 'install_model', { id: catalogId });
      current = await until(installed);
    }
    if (!ready(current)) {
      if (current.managed) {
        await deps.modes?.ensure?.(current.entry);
        current = await load();
        if (!ready(current)) {
          await start(current, 'hub_prepare_model', { id: current.entry.id });
          current = await until(ready);
        }
      } else {
        await start(current, 'runtime_setup_start', { targetId: catalogId,
          options: { installWeights: false, verifyInference: true, acceptLicenses: false, allowAdministrator: false } });
        current = await until(ready);
      }
    }
    await checkCancelled();
    if (current.managed) {
      let profileId = current.entry.profileId;
      if (!profileId) {
        const registered = await deps.register(current.entry);
        profileId = typeof registered === 'string' ? registered : registered?.profileId;
        current = await load();
        if (!ready(current)) throw Error('The local model lost its verified runtime readiness during registration.');
        if (current.entry.profileId && current.entry.profileId !== profileId) throw Error('The registered profile does not match this local model.');
      }
      if (typeof profileId !== 'string' || !/^api:[A-Za-z0-9_-]+(?::[a-f0-9]+)?$/.test(profileId))
        throw Error('The verified model has no registered chat execution profile.');
      return { profileId, catalogId };
    }
    const target = current.model.operatingProfileId || current.model.id;
    const profileId = deps.modes?.activate ? await deps.modes.activate(target, deps.modes.mode?.(current.model)) : target;
    const allowed = new Set([current.model.id, current.model.operatingProfileId, ...Object.values(current.model.modeProfiles || {})]);
    if (!allowed.has(profileId)) throw Error('The selected native execution profile is unavailable.');
    return { profileId, catalogId };
  } catch (error) {
    if (operation?.admitted) { try { await cancelOwn(); } catch {} }
    throw error;
  }
}
