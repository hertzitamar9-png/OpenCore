// Voice studio for managed text-to-speech models (Kitten TTS 2, VoxCPM2, Breeze-TTS-2).
// The bridge validates every request again; the studio only shapes the controls
// each model actually supports.
export function createManagedTts(request, refresh, catalog) {
  const REFERENCE_RATE = 24000;
  const PREFERENCES = 'opencore-tts-studio-v1';
  const ACTIVE = ['queued', 'preparing', 'running'];
  let statusCache = null;

  const readPreferences = () => {
    try { return JSON.parse(localStorage.getItem(PREFERENCES) || '{}') || {}; } catch { return {}; }
  };
  const savePreferences = (modelId, value) => {
    try { localStorage.setItem(PREFERENCES, JSON.stringify({...readPreferences(), [modelId]: value})); } catch {}
  };
  const ttsEntry = modelId => (catalog() || []).find(entry => entry.id === modelId && entry.ttsRuntime);
  async function status(force = false) {
    if (!force && statusCache && Date.now() - statusCache.at < 30000) return statusCache.value;
    const value = (await request('/tts/status')).status;
    statusCache = {at: Date.now(), value};
    return value;
  }

  function wavBase64(mono, rate) {
    const bytes = new Uint8Array(44 + mono.length * 2), view = new DataView(bytes.buffer);
    const word = (offset, text) => { for (let index = 0; index < text.length; index++) bytes[offset + index] = text.charCodeAt(index); };
    word(0, 'RIFF'); view.setUint32(4, bytes.length - 8, true); word(8, 'WAVE');
    word(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    word(36, 'data'); view.setUint32(40, mono.length * 2, true);
    for (let index = 0; index < mono.length; index++) {
      const sample = Math.max(-1, Math.min(1, mono[index]));
      view.setInt16(44 + index * 2, sample * (sample < 0 ? 32768 : 32767), true);
    }
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 16384) binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
    return btoa(binary);
  }
  async function resample(mono, from, to) {
    if (from === to) return mono;
    const source = new AudioBuffer({length: mono.length, numberOfChannels: 1, sampleRate: from});
    source.copyToChannel(mono, 0);
    const renderer = new OfflineAudioContext(1, Math.max(1, Math.ceil(mono.length * to / from)), to);
    const node = renderer.createBufferSource();
    node.buffer = source; node.connect(renderer.destination); node.start();
    return (await renderer.startRendering()).getChannelData(0);
  }
  // Trim silence around a microphone take and lift quiet recordings to a usable level.
  function tidy(mono, rate) {
    const threshold = 0.012, pad = Math.round(rate * 0.15);
    let start = 0, end = mono.length - 1;
    while (start < mono.length && Math.abs(mono[start]) < threshold) start++;
    while (end > start && Math.abs(mono[end]) < threshold) end--;
    const clip = mono.slice(Math.max(0, start - pad), Math.min(mono.length, end + pad));
    let peak = 0;
    for (const sample of clip) peak = Math.max(peak, Math.abs(sample));
    const gain = peak > 0 && peak < 0.5 ? Math.min(4, 0.9 / peak) : 1;
    if (gain !== 1) for (let index = 0; index < clip.length; index++) clip[index] *= gain;
    return clip;
  }
  async function makeReference(mono, rate, label) {
    const audio = await resample(mono, rate, REFERENCE_RATE);
    const durationS = audio.length / REFERENCE_RATE;
    if (durationS < 1) throw Error('The reference recording must be at least 1 second long.');
    if (durationS > 60) throw Error('Use a reference recording of 60 seconds or less. 5 to 15 seconds of clear speech works best.');
    const data = wavBase64(audio, REFERENCE_RATE);
    return {audio: data, durationS, label, url: 'data:audio/wav;base64,' + data, mono: audio};
  }
  async function referenceFromFile(file) {
    if (!file) throw Error('Choose an audio file.');
    if (file.size > 64 * 1024 * 1024) throw Error('Choose an audio file under 64 MB.');
    const context = new AudioContext();
    try {
      const buffer = await context.decodeAudioData(await file.arrayBuffer());
      const mono = new Float32Array(buffer.length);
      for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
        const data = buffer.getChannelData(channel);
        for (let index = 0; index < data.length; index++) mono[index] += data[index] / buffer.numberOfChannels;
      }
      return await makeReference(mono, buffer.sampleRate, file.name);
    } catch (error) {
      throw error instanceof DOMException ? Error('This audio file could not be decoded. Use WAV, MP3, FLAC, OGG or M4A.') : error;
    } finally { await context.close().catch(() => {}); }
  }
  async function startRecording(onProgress) {
    if (!navigator.mediaDevices?.getUserMedia) throw Error('Microphone recording is not available here.');
    const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
    const context = new AudioContext({sampleRate: REFERENCE_RATE});
    const source = context.createMediaStreamSource(stream), processor = context.createScriptProcessor(2048, 1, 1), mute = context.createGain();
    const chunks = [];
    let length = 0, closed = false;
    mute.gain.value = 0;
    processor.onaudioprocess = event => {
      const data = new Float32Array(event.inputBuffer.getChannelData(0));
      chunks.push(data); length += data.length;
      let sum = 0;
      for (const sample of data) sum += sample * sample;
      onProgress?.(length / context.sampleRate, Math.sqrt(sum / data.length));
    };
    source.connect(processor); processor.connect(mute); mute.connect(context.destination);
    const close = async () => {
      if (closed) return; closed = true;
      processor.onaudioprocess = null;
      try { processor.disconnect(); source.disconnect(); mute.disconnect(); } catch {}
      stream.getTracks().forEach(track => track.stop());
      await context.close().catch(() => {});
    };
    return {
      async stop() {
        const rate = context.sampleRate;
        await close();
        const mono = new Float32Array(length);
        let offset = 0;
        for (const chunk of chunks) { mono.set(chunk, offset); offset += chunk.length; }
        return makeReference(tidy(mono, rate), rate, 'Microphone recording');
      },
      cancel: close,
    };
  }
  // Fill the reference transcript with whichever dictation model the user chose.
  async function transcribe(reference) {
    const audio = wavBase64(await resample(reference.mono, REFERENCE_RATE, 16000), 16000);
    const native = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
    const invoke = (command, args) => globalThis.__opencoreInstallBridge
      ? globalThis.__opencoreInstallBridge.invoke(command, args, () => native(command, args)) : native(command, args);
    const sessionId = crypto.randomUUID();
    try {
      const started = await invoke('speech_start', {sessionId});
      const text = (await invoke('speech_transcribe', {sessionId: started || sessionId, audio}))?.text?.trim();
      if (!text) throw Error('No words were recognized in the reference recording.');
      return text;
    } catch (error) {
      void invoke('speech_cancel', {sessionId}).catch(() => {});
      throw Error('Automatic transcription failed: ' + String(error?.message || error) + ' Type the words instead, or choose a speech-to-text model for dictation in Models.');
    }
  }

  async function waitFor(id, signal) {
    for (;;) {
      if (signal?.aborted) throw signal.reason || Error('Stopped');
      const job = (await request('/tts/jobs')).jobs.find(item => item.id === id);
      if (!job) throw Error('The speech job is no longer available.');
      if (!ACTIVE.includes(job.status)) return job;
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }
  function chunks(text, limit) {
    const parts = [], sentences = text.replace(/\s+/g, ' ').trim().match(/[^.!?。！？]+[.!?。！？]*\s*/g) || [text];
    let current = '';
    for (const sentence of sentences) {
      if ((current + sentence).length > limit && current) { parts.push(current.trim()); current = ''; }
      if (sentence.length > limit) {
        for (let offset = 0; offset < sentence.length; offset += limit) parts.push(sentence.slice(offset, offset + limit).trim());
      } else current += sentence;
    }
    if (current.trim()) parts.push(current.trim());
    return parts.filter(Boolean);
  }
  // Voice calls: speak a reply with a managed model, chunked to the model's text limit.
  async function* speak(modelId, text, voice, signal) {
    const entry = ttsEntry(modelId);
    if (!entry) throw Error('Choose an installed speech model.');
    const caps = (await status()).models?.[modelId];
    if (!caps) throw Error('This speech model is not ready.');
    const {capabilities, voices} = caps, saved = readPreferences()[modelId] || {};
    const settings = {};
    const wanted = (voice || saved.voice || '').trim();
    if (capabilities.voices && wanted) {
      const match = voices.find(item => [item.id, item.voice_id].some(name => String(name || '').toLowerCase() === wanted.toLowerCase()));
      if (!match) throw Error(capabilities.label + ' has no voice named "' + wanted + '". Choose a voice in its voice studio, for example ' + (voices[0]?.id || 'Bella') + '.');
      settings.voice = match.id;
    }
    if (capabilities.instruction && (voice || saved.instruction)) settings.instruction = String(voice || saved.instruction).slice(0, 500);
    if (capabilities.presets && saved.preset && capabilities.presets.includes(saved.preset)) settings.preset = saved.preset;
    if (capabilities.cfg && saved.cfg != null) settings.cfg = saved.cfg;
    if (capabilities.steps && saved.steps != null) settings.steps = saved.steps;
    const parts = chunks(text, Math.min(capabilities.maxText, 400));
    const ids = [];
    const stop = () => ids.forEach(id => void request('/tts/cancel', {id}).catch(() => {}));
    signal?.addEventListener('abort', stop, {once: true});
    try {
      for (const part of parts) ids.push((await request('/tts/jobs', {request: {modelId, text: part, settings}})).job.id);
      for (const id of ids) {
        const job = await waitFor(id, signal);
        if (job.status !== 'completed') throw Error(job.error || 'The speech job stopped.');
        yield (await request('/tts/preview', {id})).dataUrl;
        void request('/tts/delete', {id}).catch(() => {});
      }
    } finally { signal?.removeEventListener('abort', stop); }
  }
  globalThis.__opencoreManagedTts = {owns: modelId => !!ttsEntry(modelId), speak};

  const style = `.tts-studio{display:grid;gap:12px;margin:14px 0;padding:14px;border:1px solid var(--theme-border,#527397);border-radius:10px;min-width:0}
.tts-studio h4{margin:0;font-size:15px}.tts-studio p,.tts-studio small{margin:0;overflow-wrap:anywhere}.tts-studio small{color:var(--theme-muted,#aab7c6)}
.tts-studio label{display:grid;gap:6px;min-width:0}.tts-studio textarea,.tts-studio input:not([type=checkbox]):not([type=range]):not([type=file]),.tts-studio select{width:100%;min-width:0;box-sizing:border-box;font:inherit;color:var(--theme-text,#dbe5f0);background:var(--theme-input-bg,#132435);border:1px solid var(--theme-border,#527397);border-radius:6px;padding:9px}
.tts-studio textarea{resize:vertical}.tts-studio textarea:focus,.tts-studio input:focus,.tts-studio select:focus{outline:2px solid var(--theme-accent,#6dacf5);outline-offset:1px}
.tts-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.tts-count{justify-self:end;font-size:12px}
.tts-tags{display:grid;gap:6px}.tts-tag-group{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.tts-tag-group span{font-size:12px;min-width:84px;color:var(--theme-muted,#aab7c6)}
.tts-tag-group button{min-height:30px;padding:3px 10px;border-radius:999px;font-size:12px}
.tts-reference{display:grid;gap:8px;padding:10px;border:1px dashed var(--theme-border,#527397);border-radius:8px;margin:0;min-width:0}.tts-reference legend{padding:0 6px}
.tts-reference-actions,.tts-actions,.tts-job-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.tts-reference audio,.tts-job audio{width:100%;max-width:100%}
.tts-meter{height:6px;border-radius:3px;background:var(--theme-border,#527397);overflow:hidden}.tts-meter i{display:block;height:100%;background:var(--theme-accent,#6dacf5)}
.tts-studio details{border-top:1px solid var(--theme-border,#527397);padding-top:8px}.tts-studio summary{cursor:pointer}.tts-advanced{display:grid;gap:10px;margin-top:10px}
.tts-slider{display:grid;grid-template-columns:1fr 76px;gap:8px;align-items:center}.tts-check{display:flex!important;align-items:center;gap:8px}
.tts-jobs{display:grid;gap:8px;margin:0;padding:0;list-style:none}.tts-job{display:grid;gap:6px;padding:10px;border:1px solid var(--theme-border,#527397);border-radius:8px;min-width:0}
.tts-job header{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.tts-job q{font-style:italic;overflow-wrap:anywhere}.tts-job progress{width:100%}
.tts-notice{padding:8px 10px;border-left:3px solid #d9a33a;background:rgba(217,163,58,.1);border-radius:4px}
.tts-actions button,.tts-reference-actions button{min-height:40px}`;
  let styled = false;

  return {
    speak,
    createControls(React) {
      const h = React.createElement;
      if (!styled && typeof document !== 'undefined') {
        const node = document.createElement('style'); node.textContent = style; document.head.appendChild(node); styled = true;
      }
      const genderOf = voice => /_female\b|female$/i.test(voice.voice_id || '') ? 'female' : /_male\b|male$/i.test(voice.voice_id || '') ? 'male' : '';

      function Job({job, onChanged, autoload}) {
        const [audio, setAudio] = React.useState(null), [error, setError] = React.useState(''), [working, setWorking] = React.useState(false);
        const active = ACTIVE.includes(job.status), settings = job.request?.settings || {};
        async function load() {
          setWorking(true); setError('');
          try { setAudio(await request('/tts/preview', {id: job.id})); } catch (value) { setError(String(value.message || value)); } finally { setWorking(false); }
        }
        React.useEffect(() => { if (autoload && job.status === 'completed' && !audio) void load(); }, [job.id, job.status]);
        async function act(route) {
          setWorking(true); setError('');
          try { await request(route, {id: job.id}); await onChanged(); } catch (value) { setError(String(value.message || value)); } finally { setWorking(false); }
        }
        const details = [settings.voice && 'Voice ' + settings.voice, settings.preset, settings.instruction && '“' + settings.instruction + '”',
          settings.referencePath && 'Cloned from a reference', settings.cfg != null && 'CFG ' + settings.cfg, settings.steps != null && settings.steps + ' steps',
          'Seed ' + settings.seed].filter(Boolean).join(' · ');
        return h('li', {className: 'tts-job'},
          h('header', null, h('strong', null, job.status === 'completed' ? 'Ready' + (job.result?.durationS ? ' · ' + job.result.durationS.toFixed(1) + ' s' : '')
            : job.status === 'failed' ? 'Failed' : job.status === 'cancelled' ? 'Cancelled' : job.stage || 'Working…'),
            h('small', null, (job.createdAt || '').replace('T', ' ').replace('Z', ' UTC'))),
          h('q', null, job.request?.text?.length > 220 ? job.request.text.slice(0, 220) + '…' : job.request?.text),
          h('small', null, details),
          active ? h('progress', {'aria-label': 'Generating speech'}) : null,
          job.status === 'failed' && job.error ? h('p', {role: 'alert'}, job.error) : null,
          audio ? h('audio', {controls: true, src: audio.dataUrl, preload: 'auto'}) : null,
          h('div', {className: 'tts-job-actions'},
            active ? h('button', {type: 'button', disabled: working, onClick: () => void act('/tts/cancel')}, 'Cancel') : null,
            job.status === 'completed' && !audio ? h('button', {type: 'button', disabled: working, onClick: () => void load()}, working ? 'Loading…' : 'Load audio') : null,
            audio ? h('a', {href: audio.dataUrl, download: audio.fileName, className: 'tts-download'}, 'Download WAV') : null,
            !active ? h('button', {type: 'button', disabled: working, onClick: () => void act('/tts/delete')}, 'Delete') : null),
          error ? h('p', {role: 'alert'}, error) : null);
      }

      return function ManagedTtsStudio({entry, disabled = false}) {
        const saved = React.useMemo(() => readPreferences()[entry.id] || {}, [entry.id]);
        const [state, setState] = React.useState(null), [jobs, setJobs] = React.useState([]), [error, setError] = React.useState('');
        const [text, setText] = React.useState(''), [voice, setVoice] = React.useState(saved.voice || ''), [preset, setPreset] = React.useState(saved.preset || '');
        const [instruction, setInstruction] = React.useState(saved.instruction || ''), [reference, setReference] = React.useState(null);
        const [referenceText, setReferenceText] = React.useState(''), [cfg, setCfg] = React.useState(saved.cfg ?? null), [steps, setSteps] = React.useState(saved.steps ?? null);
        const [seed, setSeed] = React.useState(saved.seed ?? 1234), [randomSeed, setRandomSeed] = React.useState(saved.randomSeed ?? true);
        const [temperature, setTemperature] = React.useState(saved.temperature ?? null), [topP, setTopP] = React.useState(saved.topP ?? null);
        const [normalize, setNormalize] = React.useState(saved.normalize ?? true), [submitting, setSubmitting] = React.useState(false);
        const [recording, setRecording] = React.useState(null), [level, setLevel] = React.useState({seconds: 0, rms: 0}), [transcribing, setTranscribing] = React.useState(false);
        const editor = React.useRef(null), recorder = React.useRef(null), alive = React.useRef(true);
        const model = state?.models?.[entry.id], caps = model?.capabilities, voices = model?.voices || [];
        const active = jobs.some(job => ACTIVE.includes(job.status));

        const loadJobs = React.useCallback(async () => {
          const value = (await request('/tts/jobs')).jobs.filter(job => job.request?.modelId === entry.id);
          if (alive.current) setJobs(value);
        }, [entry.id]);
        React.useEffect(() => {
          alive.current = true;
          status(true).then(value => { if (alive.current) setState(value); }).catch(value => alive.current && setError(String(value.message || value)));
          void loadJobs().catch(() => {});
          return () => { alive.current = false; recorder.current?.cancel(); };
        }, [entry.id]);
        React.useEffect(() => {
          if (!active) return;
          const timer = setInterval(() => { void loadJobs().catch(() => {}); }, 800);
          return () => clearInterval(timer);
        }, [active, loadJobs]);
        React.useEffect(() => {
          if (!caps) return;
          if (caps.voices && !voice && voices.length) setVoice(voices[0].id);
          if (caps.presets && !preset) setPreset(caps.presets[0]);
          if (caps.cfg && cfg == null) setCfg(caps.cfg.default);
          if (caps.steps && steps == null) setSteps(caps.steps.default);
        }, [caps, voices.length]);

        function insert(tag) {
          const node = editor.current, start = node?.selectionStart ?? text.length, end = node?.selectionEnd ?? text.length;
          let next, from, to;
          if (tag.wrap) {
            const inner = text.slice(start, end) || 'word';
            next = text.slice(0, start) + tag.wrap[0] + inner + tag.wrap[1] + text.slice(end);
            from = start + tag.wrap[0].length; to = from + inner.length;
          } else {
            next = text.slice(0, start) + tag.insert + text.slice(end);
            from = to = start + tag.insert.length;
          }
          if (caps && next.length > caps.maxText) return;
          setText(next);
          requestAnimationFrame(() => { node?.focus(); node?.setSelectionRange(from, to); });
        }
        async function startTake() {
          setError(''); setLevel({seconds: 0, rms: 0});
          try {
            recorder.current = await startRecording((seconds, rms) => {
              setLevel({seconds, rms});
              if (seconds >= 59.5 && recorder.current) void stopTake();
            });
            setRecording(true);
          } catch (value) { recorder.current = null; setError(String(value.message || value)); }
        }
        async function stopTake() {
          const current = recorder.current; recorder.current = null; setRecording(null);
          try { if (current) setReference(await current.stop()); } catch (value) { setError(String(value.message || value)); }
        }
        async function upload(event) {
          const file = event.target.files?.[0]; event.target.value = '';
          setError('');
          try { setReference(await referenceFromFile(file)); } catch (value) { setError(String(value.message || value)); }
        }
        async function autoTranscript() {
          setTranscribing(true); setError('');
          try { const value = await transcribe(reference); if (alive.current) setReferenceText(value); }
          catch (value) { if (alive.current) setError(String(value.message || value)); }
          finally { if (alive.current) setTranscribing(false); }
        }
        const transcriptRequired = caps?.reference === 'transcript-required';
        const blocked = !text.trim() ? 'Enter the words to speak.' : reference && transcriptRequired && !referenceText.trim()
          ? 'Type the exact words spoken in the reference recording (or use Fill automatically).' : '';
        async function generate() {
          setSubmitting(true); setError('');
          const useSeed = randomSeed ? Math.floor(Math.random() * 2147483647) : Number(seed);
          if (randomSeed) setSeed(useSeed);
          const settings = {seed: useSeed};
          if (caps.voices && voice && !reference) settings.voice = voice;
          if (caps.presets && preset) settings.preset = preset;
          if (caps.instruction && instruction.trim()) settings.instruction = instruction.trim();
          if (reference) { settings.referenceAudio = reference.audio; if (referenceText.trim()) settings.referenceText = referenceText.trim(); }
          if (caps.cfg && cfg != null) settings.cfg = Number(cfg);
          if (caps.steps && steps != null) settings.steps = Number(steps);
          if (caps.sampling && temperature != null) settings.temperature = Number(temperature);
          if (caps.sampling && topP != null) settings.topP = Number(topP);
          if (caps.normalize) settings.normalize = !!normalize;
          savePreferences(entry.id, {voice, preset, instruction, cfg, steps, seed: useSeed, randomSeed, temperature, topP, normalize});
          try {
            await request('/tts/jobs', {request: {modelId: entry.id, text, settings}});
            await loadJobs();
          } catch (value) { setError(String(value.message || value)); }
          finally { if (alive.current) setSubmitting(false); }
        }
        const ready = entry.status === 'installed' && entry.runtimeReady;
        const locked = disabled || !ready || !caps;
        const slider = (label, value, spec, set, hint) => h('label', null, label + (hint ? ' · ' + hint : ''),
          h('div', {className: 'tts-slider'},
            h('input', {type: 'range', min: spec.min, max: spec.max, step: spec.step || 1, value: value ?? spec.default, disabled: locked, onChange: e => set(Number(e.target.value))}),
            h('input', {type: 'number', min: spec.min, max: spec.max, step: spec.step || 1, value: value ?? spec.default, disabled: locked,
              'aria-label': label, onChange: e => set(e.target.value === '' ? null : Number(e.target.value))})));
        const groups = [];
        for (const tag of caps?.tags || []) {
          const group = groups.find(item => item.name === tag.group) || (groups.push({name: tag.group, tags: []}), groups.at(-1));
          group.tags.push(tag);
        }
        const nonCommercial = /non-?commercial/i.test(entry.license || '');

        return h('section', {className: 'tts-studio', 'aria-label': (caps?.label || entry.familyLabel) + ' voice studio'},
          h('div', null, h('h4', null, 'Voice studio'),
            h('small', null, !state ? 'Connecting to the speech runtime…' : state.loadedModelId === entry.id
              ? 'Loaded on ' + (state.device === 'cuda' ? state.gpu || 'GPU' : 'CPU') + '. Unloads after 5 idle minutes.'
              : 'Loads into memory on the first generation; this can take a minute or more on slower computers.')),
          caps?.hardware?.summary ? h('small', null, 'Runs on: ' + caps.hardware.summary + '.') : null,
          state?.loadedModelId === entry.id && state.deviceNote ? h('p', {className: 'tts-notice'}, state.deviceNote) : null,
          caps?.notes ? h('p', null, caps.notes) : null,
          nonCommercial ? h('p', {className: 'tts-notice'}, 'License: ' + entry.license + '. Do not use this model\'s audio commercially.') : null,
          h('label', null, 'Words to speak',
            h('textarea', {ref: editor, rows: 5, value: text, maxLength: caps?.maxText, disabled: locked,
              placeholder: caps?.tags?.length ? 'Type the words. Use the buttons below to add expressions.' : 'Type the words to speak.',
              onChange: e => setText(e.target.value)}),
            h('small', {className: 'tts-count'}, text.length + ' / ' + (caps?.maxText || '…'))),
          groups.length ? h('div', {className: 'tts-tags', role: 'group', 'aria-label': 'Expressions'},
            groups.map(group => h('div', {key: group.name, className: 'tts-tag-group'}, h('span', null, group.name),
              group.tags.map(tag => h('button', {key: tag.label, type: 'button', disabled: locked, title: tag.wrap ? 'Select words, then click to emphasize them' : 'Insert ' + tag.insert.trim(),
                onClick: () => insert(tag)}, tag.label))))) : null,
          h('div', {className: 'tts-row'},
            caps?.voices ? h('label', null, 'Voice',
              h('select', {value: voice, disabled: locked || !!reference, onChange: e => setVoice(e.target.value)},
                voices.map(item => h('option', {key: item.id, value: item.id}, item.id + (genderOf(item) ? ' · ' + genderOf(item) : '')))),
              reference ? h('small', null, 'The reference recording replaces the built-in voice.') : null) : null,
            caps?.presets ? h('label', null, 'Delivery',
              h('select', {value: preset, disabled: locked, onChange: e => setPreset(e.target.value)},
                caps.presets.map(item => h('option', {key: item, value: item}, item[0].toUpperCase() + item.slice(1))))) : null),
          caps?.instruction ? h('label', null, caps.instruction.label + ' (optional)',
            h('input', {type: 'text', value: instruction, maxLength: 500, disabled: locked, placeholder: caps.instruction.placeholder, onChange: e => setInstruction(e.target.value)})) : null,
          caps?.reference ? h('fieldset', {className: 'tts-reference', disabled: locked},
            h('legend', null, 'Reference voice for cloning (optional)'),
            h('small', null, 'Record or upload 5–15 seconds of one person speaking clearly. ' + (transcriptRequired
              ? 'This model needs the exact words spoken in the recording.' : 'Adding the exact words gives the closest match.')),
            h('div', {className: 'tts-reference-actions'},
              h('button', {type: 'button', onClick: () => void (recording ? stopTake() : startTake()), 'aria-pressed': !!recording},
                recording ? 'Stop recording (' + level.seconds.toFixed(1) + ' s)' : reference ? 'Record again' : 'Record from microphone'),
              h('label', {className: 'tts-upload'}, h('span', null, 'Or upload audio'),
                h('input', {type: 'file', accept: 'audio/*,.wav,.mp3,.flac,.ogg,.m4a', onChange: e => void upload(e)})),
              reference ? h('button', {type: 'button', onClick: () => { setReference(null); setReferenceText(''); }}, 'Remove reference') : null),
            recording ? h('div', {className: 'tts-meter', role: 'meter', 'aria-label': 'Microphone level', 'aria-valuemin': 0, 'aria-valuemax': 1, 'aria-valuenow': Math.min(1, level.rms * 6)},
              h('i', {style: {width: Math.min(100, level.rms * 600) + '%'}})) : null,
            reference ? h('div', null, h('small', null, reference.label + ' · ' + reference.durationS.toFixed(1) + ' s'),
              h('audio', {controls: true, src: reference.url})) : null,
            reference ? h('label', null, 'Words spoken in the reference' + (transcriptRequired ? ' (required)' : ' (optional)'),
              h('textarea', {rows: 2, value: referenceText, maxLength: 2000, onChange: e => setReferenceText(e.target.value)}),
              h('div', {className: 'tts-reference-actions'},
                h('button', {type: 'button', disabled: transcribing, onClick: () => void autoTranscript()}, transcribing ? 'Transcribing…' : 'Fill automatically'),
                h('small', null, 'Uses your dictation model. Check the words before generating.'))) : null) : null,
          h('details', null, h('summary', null, 'Advanced controls'),
            h('div', {className: 'tts-advanced'},
              caps?.cfg ? slider('Guidance (CFG)', cfg, caps.cfg, setCfg, caps.cfg.hint) : null,
              caps?.steps ? slider('Diffusion steps', steps, caps.steps, setSteps, 'more is slower and cleaner') : null,
              caps?.sampling ? slider('Temperature', temperature, {min: 0.05, max: 2, step: 0.05, default: 0.8}, setTemperature, 'higher is more varied') : null,
              caps?.sampling ? slider('Top-p', topP, {min: 0.05, max: 1, step: 0.05, default: 0.95}, setTopP) : null,
              caps?.normalize ? h('label', {className: 'tts-check'}, h('input', {type: 'checkbox', checked: !!normalize, disabled: locked, onChange: e => setNormalize(e.target.checked)}),
                'Normalize numbers and abbreviations (English)') : null,
              h('div', {className: 'tts-row'},
                h('label', null, 'Seed', h('input', {type: 'number', min: 0, max: 2147483647, step: 1, value: seed, disabled: locked || randomSeed,
                  onChange: e => setSeed(Math.max(0, Math.min(2147483647, Math.floor(Number(e.target.value) || 0))))})),
                h('label', {className: 'tts-check'}, h('input', {type: 'checkbox', checked: randomSeed, disabled: locked, onChange: e => setRandomSeed(e.target.checked)}),
                  'New random seed each time')))),
          h('div', {className: 'tts-actions'},
            h('button', {type: 'button', className: 'primary', disabled: locked || submitting || !!blocked || !!recording, onClick: () => void generate()},
              submitting ? 'Sending…' : 'Generate speech'),
            blocked && text ? h('small', null, blocked) : null),
          error ? h('p', {role: 'alert'}, error) : null,
          jobs.length ? h('ul', {className: 'tts-jobs', 'aria-label': 'Generated speech'},
            jobs.slice(0, 20).map((job, index) => h(Job, {key: job.id, job, onChanged: loadJobs, autoload: index < 4}))) : null);
      };
    },
  };
}
