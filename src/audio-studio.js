// Audio Studio: turn text into speech and speech into text with models that run on this computer.
// The App creates it once and passes its hub, installation and speech helpers; everything stays
// inside the factory because this file shares one module scope with other bundled features.
export function createAudioStudio(React, deps) {
  const h = React.createElement;
  const {useHub, useInstall, progress: visibleProgress, pause, invoke, request, Tts, Form, History} = deps;
  const RATE = 16000, PART_SECONDS = 300, MAX_SECONDS = 30 * 60, MAX_FILE_BYTES = 300 * 1024 * 1024;
  const CHOICE_KEY = 'opencore.audio-studio.voice-model';
  const transcripts = []; // This session's transcripts survive closing and reopening the studio.
  const listeners = new Set();
  let requested = null;

  // Other screens open a specific part of the studio with the shared "open studio" event.
  if (typeof window !== 'undefined') window.addEventListener('opencore-open-studio', event => {
    const detail = event.detail, category = typeof detail === 'string' ? detail : detail?.category, modelId = detail?.modelId;
    const view = category === 'voice-cloning' || category === 'tts' && modelId && !String(modelId).startsWith('hf-') ? {tab: 'more', category, modelId}
      : category === 'tts' || category === 'audio' ? {tab: 'speak', modelId} : category === 'transcribe' ? {tab: 'transcribe'} : null;
    if (!view) return;
    requested = view;
    listeners.forEach(listener => listener(view));
  });
  const takeRequest = () => { const view = requested; requested = null; return view; };

  // Whether each built-in speech model runs here (managed package, the app's own dictation, or not at all and why).
  let support = null, supportLoad = null;
  const loadSupport = () => supportLoad || (supportLoad = Promise.resolve(request ? request('/speech/support') : null)
    .then(value => (support = value?.models || {}))
    .catch(() => { supportLoad = null; return {}; }));

  const sizeText = bytes => !bytes ? '' : bytes >= 1e9 ? (bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1) + ' GB' : Math.max(1, Math.round(bytes / 1e6)) + ' MB';
  const sentence = text => {
    text = String(text || '').trim();
    return text ? text[0].toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? '' : '.') : '';
  };
  const message = error => String(error?.message || error || 'Something went wrong.').replace(/^(Error|TypeError):\s*/, '');
  const nameOf = entry => entry.familyLabel || entry.label || entry.name || String(entry.repository || entry.id).split('/').at(-1);
  const clock = seconds => Math.floor(seconds / 60) + ':' + String(Math.floor(seconds % 60)).padStart(2, '0');
  const languageName = code => {
    try { return new Intl.DisplayNames(['en'], {type: 'language'}).of(String(code).replace('_', '-')) || code; } catch { return code; }
  };
  const store = (key, value) => {
    try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch {}
    return null;
  };
  const icon = (paths, size = 18) => h('svg', {width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true, focusable: 'false'},
  ...paths.map((d, index) => h('path', {key: index, d})));
  const ICON = {
    wave: ['M3 10v4', 'M7 6v12', 'M11 3v18', 'M15 8v8', 'M19 5v14', 'M23 10v4'],
    mic: ['M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z', 'M19 10v2a7 7 0 0 1-14 0v-2', 'M12 19v3'],
    upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8l-5-5-5 5', 'M12 3v12'],
    stop: ['M7 7h10v10H7z'],
    alert: ['M12 9v4', 'M12 17h.01', 'M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'],
  };

  // ---------- installation state of one hub model ----------
  function progressFor(entry, install, hub) {
    const visible = visibleProgress(install.progress);
    if (visible?.modelId === entry.id) return visible;
    const job = hub.progress;
    if (job?.id !== entry.id || !['running', 'pausing', 'paused'].includes(job.status)) return null;
    const preparing = job.kind === 'prepare' || /^(installing|checking|loading|verifying|preparing)/.test(job.stage || '');
    return {modelId: job.id, phase: job.status === 'paused' ? 'paused' : preparing ? 'preparing' : 'downloading',
      downloadedBytes: job.bytesDone || 0, totalBytes: preparing ? 0 : job.bytesTotal || 0, currentFile: job.detail || job.currentFile || ''};
  }
  function stateOf(entry, install, hub) {
    const progress = progressFor(entry, install, hub);
    const queued = install.queue?.items?.find(item => item.modelId === entry.id && ['queued', 'starting', 'downloading', 'preparing', 'paused'].includes(item.phase));
    if (progress?.phase === 'paused' || queued?.phase === 'paused') return 'paused';
    if (install.active?.modelId === entry.id || progress && ['downloading', 'preparing', 'verifying', 'cancelling'].includes(progress.phase)) return 'installing';
    if (queued) return queued.phase === 'queued' ? 'queued' : 'installing';
    if (entry.status === 'installed' && entry.runtimeReady) return 'ready';
    if (entry.hardwareFit && !entry.hardwareFit.device) return 'blocked';
    if (progress?.phase === 'failed') return 'failed';
    return entry.status === 'installed' ? 'setup' : 'available';
  }
  const percentOf = progress => progress?.totalBytes ? Math.min(100, Math.floor(progress.downloadedBytes / progress.totalBytes * 100)) : null;
  function Chip({entry, install, hub}) {
    const state = stateOf(entry, install, hub), percent = percentOf(progressFor(entry, install, hub));
    const text = {ready: 'Ready', paused: 'Paused', queued: 'Queued', setup: 'Finish setup', blocked: 'Can’t run here', failed: 'Install failed',
      installing: percent == null ? 'Setting up…' : 'Installing ' + percent + '%', available: sizeText(entry.bytes) || 'Not installed'}[state];
    return h('span', {className: 'studio-chip is-' + state}, text);
  }

  function Empty({title, text, busy, children}) {
    return h('div', {className: 'studio-empty', role: busy ? 'status' : undefined},
      busy ? h('span', {className: 'studio-spinner', 'aria-hidden': true}) : null,
      h('h2', null, title), text ? h('p', null, text) : null, children);
  }

  // ---------- text to speech ----------
  function SpeakTab({hub, install, view, onNotice}) {
    const entries = (hub.catalog || []).filter(entry => (entry.libraryCategory || entry.category) === 'tts' && entry.ttsRuntime);
    const [choice, setChoice] = React.useState(() => view?.modelId || store(CHOICE_KEY));
    React.useEffect(() => { if (view?.modelId) setChoice(view.modelId); }, [view]);
    const choose = id => { setChoice(id); store(CHOICE_KEY, id); };
    if (!hub.loaded && !entries.length) return h(Empty, {busy: true, title: 'Loading voice models…'});
    if (!entries.length) return hub.available === false
      ? h(Empty, {title: 'The local model service is not running', text: 'It starts together with OpenCore. If this message stays, restart OpenCore.' + (hub.error ? ' Details: ' + sentence(hub.error) : '')})
      : h(Empty, {title: 'No voice models yet', text: 'Update OpenCore to get the latest voice models.'});
    const current = entries.find(entry => entry.id === choice) || entries.find(entry => stateOf(entry, install, hub) === 'ready')
      || entries.find(entry => stateOf(entry, install, hub) !== 'blocked') || entries[0];
    return h('div', {className: 'audio-layout'},
      h('nav', {className: 'audio-models', 'aria-label': 'Voice models'},
        h('h2', null, 'Voice models'),
        h('ul', null, entries.map(entry => h('li', {key: entry.id},
          h('button', {type: 'button', 'aria-current': entry === current ? 'true' : undefined, onClick: () => choose(entry.id)},
            h('span', {className: 'audio-model-name'}, nameOf(entry)),
            h(Chip, {entry, install, hub})))))),
      h(ModelPanel, {key: current.id, entry: current, entries, hub, install, onChoose: choose, onNotice}));
  }

  function ModelPanel({entry, entries, hub, install, onChoose, onNotice}) {
    const state = stateOf(entry, install, hub), progress = progressFor(entry, install, hub);
    const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
    async function run(work) {
      setBusy(true); setError('');
      try { await work(); } catch (value) { setError(message(value)); } finally { setBusy(false); }
    }
    const installModel = () => run(() => invoke('install_model', {id: entry.id}));
    const fit = entry.hardwareFit, other = install.active && install.active.modelId !== entry.id;
    const nonCommercial = /(^|[^a-z])nc([^a-z]|$)|non-?commercial/i.test(entry.license || '');
    const facts = [
      entry.bytes && ['Download', sizeText(entry.bytes)],
      fit?.device === 'cuda' && ['This computer', 'Runs on your graphics card' + (fit.gpu ? ' (' + fit.gpu + ')' : '') + '.'],
      fit?.device === 'cpu' && ['This computer', sentence(fit.reason)],
      entry.license && ['License', nonCommercial ? entry.license + ' — personal and research use only, not commercial use.' : entry.license],
    ].filter(Boolean);
    const head = h('header', {className: 'audio-panel-head'},
      h('div', null, h('h2', null, nameOf(entry)), entry.builtInDescription ? h('p', null, entry.builtInDescription) : null),
      h(Chip, {entry, install, hub}));
    if (state === 'ready') return h('div', {className: 'audio-panel is-ready'}, head,
      h(Tts, {entry, disabled: !!install.active && visibleProgress(install.progress)?.phase === 'preparing'}));

    const percent = percentOf(progress);
    let body;
    if (state === 'blocked') {
      const alternative = entries.find(item => item !== entry && stateOf(item, install, hub) === 'ready')
        || entries.find(item => item !== entry && stateOf(item, install, hub) !== 'blocked');
      body = h('div', {className: 'studio-callout is-warning', role: 'note'},
        icon(ICON.alert, 20),
        h('div', null,
          h('strong', null, 'This computer can’t run ' + nameOf(entry)),
          h('p', null, sentence(fit.reason)),
          alternative ? h('p', null, nameOf(alternative) + ' works on this computer.') : null,
          alternative ? h('button', {type: 'button', onClick: () => onChoose(alternative.id)}, 'Use ' + nameOf(alternative)) : null));
    } else if (state === 'installing') {
      const preparing = progress?.phase === 'preparing' || percent == null;
      body = h('div', {className: 'studio-progress-block'},
        h('div', {className: 'studio-progress-label'},
          h('strong', null, preparing ? 'Setting up the voice engine…' : 'Downloading… ' + percent + '%'),
          !preparing ? h('span', null, sizeText(progress.downloadedBytes) + ' of ' + sizeText(progress.totalBytes)) : null),
        h('div', {className: 'studio-progress' + (preparing ? ' is-indeterminate' : ''), role: 'progressbar', 'aria-label': 'Installing ' + nameOf(entry),
          'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': preparing ? undefined : percent},
        h('i', {style: preparing ? undefined : {width: percent + '%'}})),
        h('p', {className: 'studio-hint'}, preparing ? 'OpenCore is installing and testing what this model needs. This happens once.'
          : 'You can keep using OpenCore while this downloads.' + (progress?.currentFile ? ' Current file: ' + progress.currentFile + '.' : '')),
        h('div', {className: 'studio-actions'},
          h('button', {type: 'button', disabled: busy || install.active?.pausing || install.active?.kind === 'starting', onClick: () => run(pause)},
            install.active?.pausing ? 'Pausing…' : 'Pause')));
    } else if (state === 'queued') {
      body = h('p', {className: 'studio-hint'}, 'Waiting for the current download to finish. It starts automatically.');
    } else if (state === 'setup') {
      body = h('div', {className: 'studio-step'},
        h('p', null, 'Downloaded. One last step: OpenCore sets up and tests the voice engine on this computer. This happens once and can take a few minutes.'),
        h('div', {className: 'studio-actions'},
          h('button', {type: 'button', className: 'primary', disabled: busy || other, onClick: () => run(() => invoke('hub_prepare_model', {id: entry.id}))},
            busy ? 'Starting…' : 'Finish setup')),
        other ? h('p', {className: 'studio-hint'}, 'Another model is installing. Finish setup when it is done.') : null);
    } else {
      const blockedByOther = other && !install.queue;
      body = h('div', {className: 'studio-step'},
        state === 'paused' ? h('p', null, 'Download paused' + (percent != null ? ' at ' + percent + '%' : '') + '. Resume to continue where it stopped.')
          : h('p', null, 'OpenCore downloads the model and installs everything it needs. There is nothing else to set up.'),
        state === 'failed' && progress?.error ? h('p', {className: 'studio-callout is-error', role: 'alert'}, sentence(progress.error)) : null,
        h('div', {className: 'studio-actions'},
          h('button', {type: 'button', className: 'primary', disabled: busy || blockedByOther, onClick: installModel},
            busy ? 'Starting…' : state === 'paused' ? 'Resume download' : state === 'failed' ? 'Try again' : 'Install' + (entry.bytes ? ' · ' + sizeText(entry.bytes) : ''))),
        other ? h('p', {className: 'studio-hint'}, blockedByOther ? 'Another model is installing. You can install this one when it finishes.'
          : 'It will start after the current download.') : null);
    }
    return h('div', {className: 'audio-panel'}, head,
      facts.length ? h('dl', {className: 'audio-facts'}, facts.map(([label, value]) => h(React.Fragment, {key: label}, h('dt', null, label), h('dd', null, value)))) : null,
      body,
      error ? h('p', {className: 'studio-callout is-error', role: 'alert'}, error) : null,
      state !== 'ready' ? h('p', {className: 'studio-hint'}, 'Speech is generated on this computer. Your text and audio are not sent anywhere.') : null);
  }

  // ---------- speech to text ----------
  async function decodeAudio(blob) {
    if (blob.size > MAX_FILE_BYTES) throw Error('This file is larger than 300 MB. Use a shorter recording or a compressed format such as MP3.');
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context || !window.OfflineAudioContext) throw Error('Audio decoding is not available in this window.');
    const context = new Context();
    let buffer;
    try { buffer = await context.decodeAudioData(await blob.arrayBuffer()); }
    catch { throw Error('This file could not be read as audio. Use MP3, WAV, M4A, OGG, FLAC or WebM.'); }
    finally { void context.close().catch(() => {}); }
    if (buffer.duration > MAX_SECONDS + 1)
      throw Error('This audio is ' + Math.round(buffer.duration / 60) + ' minutes long. Audio Studio transcribes up to ' + MAX_SECONDS / 60 + ' minutes at a time, so trim or split it first.');
    const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(buffer.duration * RATE)), RATE);
    const source = offline.createBufferSource();
    source.buffer = buffer; source.connect(offline.destination); source.start();
    return (await offline.startRendering()).getChannelData(0);
  }
  // Long audio is sent in parts of about five minutes, each cut at the quietest moment near its end.
  function splitAudio(samples) {
    const parts = [], frame = RATE / 50;
    let start = 0;
    while (start < samples.length) {
      let end = Math.min(samples.length, start + PART_SECONDS * RATE);
      if (end < samples.length) {
        let quietest = Infinity, cut = end;
        for (let at = end - 5 * RATE; at + frame <= end; at += frame) {
          let sum = 0;
          for (let i = at; i < at + frame; i++) sum += samples[i] * samples[i];
          if (sum < quietest) { quietest = sum; cut = at + frame / 2; }
        }
        end = cut;
      }
      parts.push(samples.subarray(start, end));
      start = end;
    }
    return parts;
  }
  function wavBlob(samples) {
    const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
    const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVEfmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, RATE, true);
    view.setUint32(28, RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
    return new Blob([buffer], {type: 'audio/wav'});
  }
  const base64 = blob => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).slice(String(reader.result).indexOf(',') + 1));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
  async function transcribePart(samples) {
    const audio = await base64(wavBlob(samples));
    const sessionId = globalThis.crypto?.randomUUID?.() || Date.now() + '-' + Math.random().toString(16).slice(2);
    let id = sessionId;
    try {
      const started = await invoke('speech_start', {sessionId});
      if (typeof started === 'string' && started) id = started;
      return await invoke('speech_transcribe', {sessionId: id, audio});
    } catch (error) {
      void Promise.resolve(invoke('speech_cancel', {sessionId: id})).catch(() => {});
      throw error;
    }
  }

  function TranscribeTab({hub, install, library, onNotice, onLibrary}) {
    const [status, setStatus] = React.useState(null), [statusError, setStatusError] = React.useState('');
    const [busy, setBusy] = React.useState(''), [error, setError] = React.useState(''), [copied, setCopied] = React.useState(false);
    const [recording, setRecording] = React.useState(null), [dragging, setDragging] = React.useState(false);
    const [items, setItems] = React.useState(() => transcripts.slice()), [selected, setSelected] = React.useState(() => transcripts[0]?.id ?? null);
    const recorder = React.useRef(null), alive = React.useRef(true), fileInput = React.useRef(null);
    const loadStatus = React.useCallback(async () => {
      try { const value = await invoke('speech_status', {}); if (alive.current) { setStatus(value || null); setStatusError(''); } }
      catch (value) { if (alive.current) setStatusError(message(value)); }
    }, []);
    React.useEffect(() => {
      alive.current = true; void loadStatus();
      return () => { alive.current = false; recorder.current?.cancel(); };
    }, [loadStatus]);

    const native = library.filter(model => !model.hubModel && model.backend !== 'hub' && model.category === 'speech');
    const hubSpeech = (hub.catalog || []).filter(entry => (entry.libraryCategory || entry.category) === 'speech');
    const choices = [...native.filter(model => model.installed).map(model => ({id: model.id, label: model.label || model.id})),
      ...hubSpeech.filter(entry => entry.status === 'installed' && entry.runtimeReady).map(entry => ({id: entry.id, label: nameOf(entry)}))];
    const installable = [...native.filter(model => !model.installed && model.installable !== false).map(model => ({id: model.id, label: model.label || model.id, bytes: model.downloadBytes || model.totalBytes})),
      ...hubSpeech.filter(entry => entry.status !== 'installed' && entry.installable !== false && !(entry.hardwareFit && !entry.hardwareFit.device))
        .map(entry => ({id: entry.id, label: nameOf(entry), bytes: entry.bytes}))].sort((a, b) => (a.bytes || 0) - (b.bytes || 0)).slice(0, 4);
    const currentId = status?.modelId && choices.some(choice => choice.id === status.modelId) ? status.modelId : null;
    const ready = !!currentId && status?.installed !== false && status?.enabled !== false;
    const result = items.find(item => item.id === selected) || null;

    async function act(work) {
      setError('');
      try { await work(); } catch (value) { setError(message(value)); }
      await loadStatus();
    }
    async function transcribe(blob, source) {
      setBusy('Preparing audio…'); setError(''); setCopied(false);
      try {
        const samples = await decodeAudio(blob);
        if (samples.length < RATE * 0.4) throw Error('The audio is too short. Record at least a second of speech.');
        const parts = splitAudio(samples), texts = [];
        let language = '';
        for (const [index, part] of parts.entries()) {
          if (alive.current) setBusy(parts.length > 1 ? 'Transcribing part ' + (index + 1) + ' of ' + parts.length + '…' : 'Transcribing…');
          const value = await transcribePart(part);
          texts.push(String(value?.text || '').trim());
          language = language || value?.language || '';
        }
        const item = {id: Date.now(), source, text: texts.filter(Boolean).join(' '), language, seconds: samples.length / RATE, at: new Date()};
        transcripts.unshift(item); transcripts.splice(20);
        if (alive.current) { setItems(transcripts.slice()); setSelected(item.id); if (!item.text) setError('No speech was recognized in this audio.'); }
      } catch (value) {
        if (alive.current) setError(message(value));
      } finally {
        if (alive.current) setBusy('');
        void loadStatus();
      }
    }
    async function startRecording() {
      setError('');
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}}); }
      catch (value) {
        setError(value?.name === 'NotAllowedError' ? 'OpenCore is not allowed to use the microphone. Allow microphone access in your system privacy settings, then try again.'
          : value?.name === 'NotFoundError' ? 'No microphone was found. Connect one and try again.' : message(value));
        return;
      }
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(value => window.MediaRecorder?.isTypeSupported?.(value));
      let media;
      try { media = new MediaRecorder(stream, type ? {mimeType: type} : undefined); }
      catch (value) { stream.getTracks().forEach(track => track.stop()); setError(message(value)); return; }
      const chunks = [], started = Date.now(), context = new (window.AudioContext || window.webkitAudioContext)(), analyser = context.createAnalyser();
      analyser.fftSize = 1024; context.createMediaStreamSource(stream).connect(analyser);
      const wave = new Float32Array(analyser.fftSize);
      let finished = false;
      const finish = keep => {
        if (finished) return; finished = true;
        clearInterval(timer);
        media.onstop = () => {
          stream.getTracks().forEach(track => track.stop()); void context.close().catch(() => {});
          recorder.current = null;
          if (alive.current) setRecording(null);
          if (keep && chunks.length) void transcribe(new Blob(chunks, {type: media.mimeType || type || 'audio/webm'}), 'Recording');
        };
        if (media.state === 'inactive') media.onstop(); else media.stop();
      };
      const timer = setInterval(() => {
        analyser.getFloatTimeDomainData(wave);
        let sum = 0;
        for (const value of wave) sum += value * value;
        const seconds = (Date.now() - started) / 1000;
        if (alive.current) setRecording({seconds, level: Math.min(1, Math.sqrt(sum / wave.length) * 5)});
        if (seconds >= MAX_SECONDS) finish(true);
      }, 150);
      media.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.current = {stop: () => finish(true), cancel: () => finish(false)};
      media.start(1000);
      setRecording({seconds: 0, level: 0});
    }
    function pick(files) {
      const file = files?.[0];
      if (file) void transcribe(file, file.name);
    }
    function updateText(text) {
      const item = transcripts.find(value => value.id === selected);
      if (item) item.text = text;
      setItems(transcripts.slice());
    }
    async function copy() {
      try { await navigator.clipboard.writeText(result.text); setCopied(true); setTimeout(() => alive.current && setCopied(false), 1600); }
      catch { setError('The transcript could not be copied. Select the text and copy it instead.'); }
    }
    function save() {
      const link = document.createElement('a'), stamp = new Date(result.at).toISOString().slice(0, 16).replace(/[:T]/g, '-');
      link.href = URL.createObjectURL(new Blob([result.text], {type: 'text/plain;charset=utf-8'}));
      link.download = 'transcript-' + stamp + '.txt';
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }
    function remove() {
      const index = transcripts.findIndex(value => value.id === selected);
      if (index >= 0) transcripts.splice(index, 1);
      setItems(transcripts.slice()); setSelected(transcripts[0]?.id ?? null);
    }

    let setup = null;
    if (!status && !statusError) setup = h(Empty, {busy: true, title: 'Checking speech recognition…'});
    else if (!choices.length) setup = h(Empty, {title: 'Install a speech recognition model',
      text: 'A speech recognition model turns recordings into text on this computer. Pick one to install; smaller models are faster, larger ones are more accurate.'},
    installable.length ? h('ul', {className: 'studio-install-list'}, installable.map(model => h('li', {key: model.id},
      h('span', null, h('strong', null, model.label), model.bytes ? h('small', null, sizeText(model.bytes)) : null),
      h('button', {type: 'button', className: 'primary', disabled: !!install.active && !install.queue,
        onClick: () => act(async () => { await invoke('install_model', {id: model.id}); onNotice('Installing ' + model.label + '. Progress also appears in Models.'); await onLibrary(); })}, 'Install')))) : null,
    h('div', {className: 'studio-actions'}, h('button', {type: 'button', onClick: () => window.dispatchEvent(new CustomEvent('opencore-open-models'))}, 'Browse all models')));
    else if (!currentId) setup = h(Empty, {title: 'Choose a speech recognition model', text: 'Pick which installed model turns your audio into text.'},
      h('div', {className: 'studio-actions'}, choices.map(choice => h('button', {key: choice.id, type: 'button',
        onClick: () => act(() => invoke('speech_set_model', {modelId: choice.id}))}, choice.label))));
    else if (status?.enabled === false) setup = h(Empty, {title: 'Speech to text is turned off',
      text: 'Turn it on to transcribe recordings and use the chat microphone. The model loads only while you use it.'},
    h('div', {className: 'studio-actions'}, h('button', {type: 'button', className: 'primary', onClick: () => act(() => invoke('speech_set_enabled', {enabled: true}))}, 'Turn on speech to text')));

    const working = !!busy || !!recording;
    return h('div', {className: 'audio-transcribe'},
      ready || choices.length && currentId ? h('div', {className: 'audio-toolbar'},
        h('label', null, h('span', null, 'Model'),
          h('select', {value: currentId || '', disabled: working, onChange: event => act(() => invoke('speech_set_model', {modelId: event.target.value}))},
            choices.map(choice => h('option', {key: choice.id, value: choice.id}, choice.label)))),
        h('small', null, status?.phase === 'loading' ? 'Loading the model…' : status?.workerReady ? 'Ready' : 'Loads when you start')) : null,
      setup || h('section', {className: 'audio-card audio-capture' + (dragging ? ' is-dragging' : ''), 'aria-label': 'Record or upload audio',
        onDragOver: event => { if (working) return; event.preventDefault(); setDragging(true); },
        onDragLeave: () => setDragging(false),
        onDrop: event => { event.preventDefault(); setDragging(false); if (!working) pick(event.dataTransfer?.files); }},
      recording ? h('div', {className: 'audio-recording', role: 'status'},
        h('span', {className: 'audio-rec-dot', 'aria-hidden': true}),
        h('strong', null, 'Recording ' + clock(recording.seconds)),
        h('div', {className: 'studio-meter', 'aria-hidden': true}, h('i', {style: {width: Math.round(recording.level * 100) + '%'}})),
        h('div', {className: 'studio-actions'},
          h('button', {type: 'button', className: 'primary', onClick: () => recorder.current?.stop()}, icon(ICON.stop, 16), 'Stop and transcribe'),
          h('button', {type: 'button', onClick: () => recorder.current?.cancel()}, 'Discard')))
        : busy ? h('div', {className: 'audio-busy', role: 'status'}, h('span', {className: 'studio-spinner', 'aria-hidden': true}), h('strong', null, busy),
          h('small', null, 'Long recordings take a while. You can switch to other parts of OpenCore meanwhile.'))
          : h(React.Fragment, null,
            h('div', {className: 'audio-capture-actions'},
              h('button', {type: 'button', className: 'primary audio-record', onClick: () => void startRecording()}, icon(ICON.mic, 20), 'Record'),
              h('button', {type: 'button', onClick: () => fileInput.current?.click()}, icon(ICON.upload, 18), 'Upload audio')),
            h('p', {className: 'studio-hint'}, 'Or drop an audio or video file here. Up to ' + MAX_SECONDS / 60 + ' minutes; MP3, WAV, M4A, OGG, FLAC and WebM work.')),
      h('input', {ref: fileInput, type: 'file', hidden: true, accept: 'audio/*,video/mp4,video/webm,.mp3,.wav,.m4a,.ogg,.oga,.opus,.flac,.webm,.aac',
        onChange: event => { pick(event.target.files); event.target.value = ''; }})),
      statusError && !error ? h('p', {className: 'studio-callout is-error', role: 'alert'}, statusError) : null,
      error ? h('p', {className: 'studio-callout is-error', role: 'alert'}, error) : null,
      result ? h('section', {className: 'audio-card audio-result', 'aria-label': 'Transcript'},
        h('header', null, h('h3', null, 'Transcript'),
          h('small', null, [result.source, clock(result.seconds), result.language && languageName(result.language)].filter(Boolean).join(' · '))),
        h('textarea', {value: result.text, rows: 8, 'aria-label': 'Transcript text', onChange: event => updateText(event.target.value)}),
        h('div', {className: 'studio-actions'},
          h('button', {type: 'button', className: 'primary', disabled: !result.text, onClick: () => void copy()}, copied ? 'Copied' : 'Copy text'),
          h('button', {type: 'button', disabled: !result.text, onClick: save}, 'Save as .txt'),
          h('button', {type: 'button', onClick: remove}, 'Delete'))) : null,
      items.length > 1 ? h('section', {className: 'audio-card audio-history', 'aria-label': 'Earlier transcripts'},
        h('h3', null, 'Earlier in this session'),
        h('ul', null, items.map(item => h('li', {key: item.id},
          h('button', {type: 'button', 'aria-current': item.id === selected ? 'true' : undefined, onClick: () => setSelected(item.id)},
            h('strong', null, item.source), h('span', null, item.text ? item.text.slice(0, 90) + (item.text.length > 90 ? '…' : '') : 'No speech recognized'))))))
        : null);
  }

  // ---------- voice models without a managed package ----------
  const MORE = [['tts', 'Speech synthesis'], ['voice-cloning', 'Reference voice']];
  function Unsupported({modelId, item, hub, library, onOpen}) {
    const model = library.find(entry => entry.id === modelId), name = model?.label || modelId;
    const options = (item.use || []).map(id => {
      const entry = (hub.catalog || []).find(candidate => candidate.id === id);
      if (entry) return {id, name: nameOf(entry), view: {tab: 'speak', modelId: id}};
      const native = library.find(candidate => candidate.id === id);
      return native ? {id, name: native.label || id, view: {tab: 'transcribe'}} : null;
    }).filter(Boolean).slice(0, 3);
    return h('div', {className: 'studio-callout is-warning', role: 'note'},
      icon(ICON.alert, 20),
      h('div', null,
        h('strong', null, 'OpenCore can’t run ' + name),
        h('p', null, sentence(item.reason || 'There is no tested runtime for this model yet.')),
        options.length ? h('div', {className: 'studio-actions'}, options.map(option =>
          h('button', {key: option.id, type: 'button', onClick: () => onOpen(option.view)}, 'Use ' + option.name))) : null));
  }
  function MoreTab({category, modelId, support, hub, library, onCategory, onOpen, onNotice}) {
    const [revision, setRevision] = React.useState(0), label = (MORE.find(([id]) => id === category) || MORE[0])[1];
    const item = modelId && support?.[modelId];
    return h('div', {className: 'audio-more'},
      item?.state === 'unavailable' ? h(Unsupported, {modelId, item, hub, library, onOpen}) : null,
      h('p', {className: 'studio-hint'}, 'Voice models OpenCore runs itself are under Text to speech. Here you can connect a compatible runtime of your own for other voice models.'),
      h('div', {className: 'model-category-tabs', role: 'group', 'aria-label': 'Voice model type'},
        MORE.map(([id, text]) => h('button', {key: id, type: 'button', 'aria-pressed': category === id, onClick: () => onCategory(id)}, text))),
      h(Form, {key: category, category, label, onNotice, onSubmitted: () => setRevision(value => value + 1)}),
      h(History, {category, revision, onNotice}));
  }

  function Tabs({tabs, value, onChange}) {
    const nodes = React.useRef([]);
    return h('div', {className: 'studio-tabs', role: 'tablist', 'aria-label': 'Audio Studio sections'}, tabs.map(([id, label], index) =>
      h('button', {key: id, type: 'button', role: 'tab', id: 'audio-tab-' + id, 'aria-selected': value === id, 'aria-controls': 'audio-studio-panel',
        tabIndex: value === id ? 0 : -1, ref: node => { nodes.current[index] = node; }, onClick: () => onChange(id),
        onKeyDown: event => {
          const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (!step) return;
          event.preventDefault();
          const next = (index + step + tabs.length) % tabs.length;
          onChange(tabs[next][0]); nodes.current[next]?.focus();
        }}, label)));
  }

  return function AudioStudio({onNotice = () => {}}) {
    const hub = useHub(), install = useInstall();
    const [view, setView] = React.useState(() => takeRequest() || {tab: store('opencore.audio-studio.tab') === 'transcribe' ? 'transcribe' : 'speak'});
    const [library, setLibrary] = React.useState(() => globalThis.__opencoreModelLibrary?.peek?.()?.models || []);
    React.useEffect(() => {
      const listener = next => { requested = null; setView(next); };
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    }, []);
    const loadLibrary = React.useCallback(async () => {
      try { const value = await invoke('list_model_library', {}); if (Array.isArray(value?.models)) setLibrary(value.models); } catch {}
    }, []);
    React.useEffect(() => { void loadLibrary(); }, [loadLibrary, install.terminal, install.receiptsRevision]);
    const [supported, setSupported] = React.useState(support);
    React.useEffect(() => { let live = true; void loadSupport().then(value => { if (live) setSupported(value); }); return () => { live = false; }; }, []);
    // A built-in model that now runs as a managed package or through dictation opens where it works.
    React.useEffect(() => {
      const item = view.tab === 'more' && view.modelId && supported?.[view.modelId];
      if (item?.state === 'managed' && item.use?.[0]) setView({tab: 'speak', modelId: item.use[0]});
      else if (item?.state === 'native') setView({tab: 'transcribe'});
    }, [view, supported]);
    const more = view.tab === 'more' || library.some(model => !model.hubModel && model.backend !== 'hub' && model.installed && ['tts', 'voice-cloning'].includes(model.category));
    const tabs = [['speak', 'Text to speech'], ['transcribe', 'Speech to text'], ...more ? [['more', 'More voice models']] : []];
    const tab = tabs.some(([id]) => id === view.tab) ? view.tab : 'speak';
    const select = id => { setView(current => ({...current, tab: id, modelId: undefined})); if (id !== 'more') store('opencore.audio-studio.tab', id); };
    return h('section', {className: 'audio-studio', 'aria-label': 'Audio Studio'},
      h('header', {className: 'studio-header'},
        h('span', {className: 'studio-header-icon', 'aria-hidden': true}, icon(ICON.wave, 22)),
        h('div', null, h('h1', null, 'Audio Studio'),
          h('p', null, 'Turn text into natural speech and recordings into text. Everything runs on this computer.'))),
      h(Tabs, {tabs, value: tab, onChange: select}),
      h('div', {className: 'audio-studio-panel', role: 'tabpanel', id: 'audio-studio-panel', 'aria-labelledby': 'audio-tab-' + tab},
        tab === 'speak' ? h(SpeakTab, {hub, install, view, onNotice})
          : tab === 'transcribe' ? h(TranscribeTab, {hub, install, library, onNotice, onLibrary: loadLibrary})
            : h(MoreTab, {category: view.category === 'voice-cloning' ? 'voice-cloning' : 'tts', modelId: view.modelId, support: supported, hub, library,
              onCategory: category => setView({tab: 'more', category}), onOpen: next => setView(next), onNotice})));
  };
}
