// Route the shared microphone to the separately managed Phonon decoder.
export function createPhononSpeech(request, refresh) {
  let managed = false;
  const sessions = new Set();
  async function wavAudio(encoded) {
    const raw = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    if (raw.length > 32 * 1024 * 1024) throw Error('The recording is too large. Record a shorter message.');
    const context = new AudioContext();
    try {
      const buffer = await context.decodeAudioData(raw.buffer);
      if (!buffer.duration || buffer.duration > 600) throw Error('Record up to ten minutes at a time.');
      const renderer = new OfflineAudioContext(1, Math.ceil(buffer.duration * 16000), 16000);
      const source = renderer.createBufferSource();
      source.buffer = buffer;
      source.connect(renderer.destination);
      source.start();
      const mono = (await renderer.startRendering()).getChannelData(0);
      const bytes = new Uint8Array(44 + mono.length * 2);
      const view = new DataView(bytes.buffer);
      const word = (offset, text) => {
        for (let index = 0; index < text.length; index++) bytes[offset + index] = text.charCodeAt(index);
      };
      word(0, 'RIFF'); view.setUint32(4, bytes.length - 8, true); word(8, 'WAVE');
      word(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
      view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
      view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
      word(36, 'data'); view.setUint32(40, mono.length * 2, true);
      for (let index = 0; index < mono.length; index++) {
        const sample = Math.max(-1, Math.min(1, mono[index]));
        view.setInt16(44 + index * 2, sample * (sample < 0 ? 32768 : 32767), true);
      }
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 16384) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 16384));
      }
      return btoa(binary);
    } finally {
      await context.close();
    }
  }

  return {
    createControls(React) {
      const h = React.createElement;
      return function ManagedSpeechControls({entry, disabled = false}) {
        const [speech, setSpeech] = React.useState(null);
        const [pending, setPending] = React.useState(false);
        const [error, setError] = React.useState('');
        const [connectionError, setConnectionError] = React.useState('');
        const alive = React.useRef(true);
        const mutation = React.useRef(0);
        React.useEffect(() => {
          alive.current = true;
          let active = true;
          async function load() {
            const revision = mutation.current;
            if (revision % 2) return;
            try {
              const value = (await request('/speech/status')).status;
              if (active && revision === mutation.current) { managed = !!value; setSpeech(value); setConnectionError(''); }
            } catch (error) {
              if (active && revision === mutation.current) setConnectionError(String(error.message || error));
            }
          }
          void load();
          const timer = setInterval(load, 1500);
          return () => { active = false; alive.current = false; clearInterval(timer); };
        }, [entry.id]);
        const selected = speech?.modelId === entry.id;
        const enabled = selected && speech.enabled;
        const ready = entry.status === 'installed' && entry.runtimeReady;
        const locked = disabled || pending || !ready;
        async function change(route, args) {
          mutation.current++;
          setPending(true); setError('');
          try {
            if (!selected) {
              await request('/speech/select', {id: entry.id});
              managed = true;
              sessions.clear();
            }
            const value = await request(route, args);
            if (alive.current) setSpeech(value);
            await refresh();
          } catch (error) {
            if (alive.current) setError(String(error.message || error));
          } finally {
            mutation.current++;
            if (alive.current) setPending(false);
          }
        }
        const starting = selected && speech.phase === 'loading';
        return h('div', {className: 'whisper-controls', 'aria-label': (entry.familyLabel || 'Phonon-2.5') + ' speech settings'},
          h('div', {className: 'whisper-enable-row'},
            h('div', null, h('strong', null, 'Microphone dictation'),
              h('small', null, 'Runs locally on CPU. RAM standby keeps weights ready between recordings.')),
            h('button', {type: 'button', role: 'switch', 'aria-checked': !!enabled, 'aria-label': 'Speech to text',
              className: 'whisper-toggle' + (enabled ? ' on' : ''), disabled: locked,
              onClick: () => void change('/speech/enabled', {enabled: !enabled})}, h('span'), enabled ? 'On' : 'Off')),
          h('fieldset', {disabled: locked || starting || selected && ['recording', 'transcribing'].includes(speech.phase)},
            h('legend', null, 'When the microphone starts'),
            h('label', null,
              h('input', {type: 'radio', name: 'phonon25-idle-' + entry.id, checked: speech?.idleMode === 'cold',
                onChange: () => void change('/speech/idle-mode', {idleMode: 'cold'})}),
              h('span', null, h('strong', null, 'Load from disk each time'),
                h('small', null, 'Cold start · ' + (selected && speech.coldStartMs != null
                  ? 'about ' + (speech.coldStartMs / 1000).toFixed(2) + ' s on this device' : 'startup time is measured on first use')))),
            h('label', null,
              h('input', {type: 'radio', name: 'phonon25-idle-' + entry.id, checked: (speech?.idleMode || 'ram') === 'ram',
                onChange: () => void change('/speech/idle-mode', {idleMode: 'ram'})}),
              h('span', null, h('strong', null, 'Keep sleeping in RAM'),
                h('small', null, 'Recommended for frequent dictation · weights stay in system RAM between recordings.')))),
          h('p', {className: 'whisper-runtime-status', role: 'status'}, !ready
            ? 'Finish installing this speech model to enable the microphone.'
            : pending ? 'Saving speech settings…' : !enabled ? 'Speech is off. The model stays installed on disk.'
            : starting ? 'Loading the speech model…'
            : speech.phase === 'recording' ? 'Microphone recording is active.'
            : speech.phase === 'transcribing' ? 'Transcribing the recording…'
            : speech.idleMode === 'cold' ? 'Loads from disk when you click the microphone.'
            : speech.workerReady ? 'Sleeping in system RAM; ready for dictation.' : 'RAM standby loads before the next recording.'),
          error || connectionError ? h('p', {role: 'alert'}, error || connectionError) : null);
      };
    },
    async select(entry) {
      if (entry.runtime?.adapter !== 'phonon25') return false;
      await request('/speech/select', {id: entry.id});
      managed = true;
      sessions.clear();
      await refresh();
      return true;
    },
    async invoke(command, args = {}, native) {
      if (!command.startsWith('speech_')) return {handled: false};
      if (command === 'speech_set_model') {
        if (args.modelId?.startsWith('hf-')) {
          const value = await request('/speech/select', {id: args.modelId});
          managed = true;
          sessions.clear();
          return {handled: true, value};
        }
        const value = await native();
        await request('/speech/select', {id: null});
        managed = false;
        sessions.clear();
        return {handled: true, value};
      }
      if (sessions.has(args.sessionId)) {
        if (command === 'speech_cancel') {
          const value = await request('/speech/cancel', args);
          sessions.delete(args.sessionId);
          return {handled: true, value};
        }
        if (command === 'speech_transcribe') {
          return {handled: true, value: await request('/speech/transcribe', {...args, audio: await wavAudio(args.audio)})};
        }
      }
      let status;
      try { status = (await request('/speech/status')).status; }
      catch (error) {
        if (managed) throw error;
        return {handled: false};
      }
      managed = !!status;
      if (!status) return {handled: false};
      let value;
      if (command === 'speech_status' || command === 'speech_set_runtime_precision') value = status;
      else if (command === 'speech_set_enabled') value = await request('/speech/enabled', args);
      else if (command === 'speech_set_idle_mode') value = await request('/speech/idle-mode', args);
      else if (command === 'speech_start') {
        const sessionId = args.sessionId || crypto.randomUUID();
        sessions.add(sessionId);
        try { value = await request('/speech/start', {sessionId}); }
        catch (error) { sessions.delete(sessionId); throw error; }
      }
      else if (command === 'speech_cancel') value = await request('/speech/cancel', args);
      else if (command === 'speech_transcribe') {
        value = await request('/speech/transcribe', {...args, audio: await wavAudio(args.audio)});
      } else return {handled: false};
      return {handled: true, value};
    },
  };
}
