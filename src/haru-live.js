const haruRate = 24000, haruSamples = 3200;

export function encodeHaruFrame(frame) {
  if (frame.length !== haruSamples || !frame.every(Number.isFinite)) throw Error('Invalid Haru audio frame.');
  const bytes = new Uint8Array(frame.length * 2), pcm = new DataView(bytes.buffer);
  for (let i = 0; i < frame.length; i++) pcm.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, frame[i])) * 32767), true);
  return btoa(String.fromCharCode(...bytes));
}

export class HaruAudioBuffer {
  constructor() { this.samples = new Float32Array(); this.muted = false; }
  append(frame) {
    if (this.muted) return;
    if (this.samples.length + frame.length > haruRate * 4)
      throw Error('Haru fell behind the microphone by four seconds. Free GPU memory and restart the call.');
    const next = new Float32Array(this.samples.length + frame.length);
    next.set(this.samples); next.set(frame, this.samples.length); this.samples = next;
  }
  mute(value) { this.muted = value; this.samples = new Float32Array(); }
  take() {
    if (this.muted) return new Float32Array(haruSamples);
    if (this.samples.length < haruSamples) return null;
    const result = this.samples.slice(0, haruSamples); this.samples = this.samples.slice(haruSamples); return result;
  }
}

export async function startHaruLive({ id, reference, persona, context, signal, onTranscript, onError }) {
  if (context.sampleRate !== haruRate) throw Error('Haru requires a 24 kHz audio context.');
  signal.throwIfAborted();
  const descriptor = (await window.__TAURI_INTERNALS__.invoke('runtime_setup_probe'))?.hubBridge;
  if (!descriptor?.available || !/^http:\/\/127\.0\.0\.1:\d+$/.test(descriptor.url) || !descriptor.secret)
    throw Error(descriptor?.reason || 'The managed audio service is unavailable.');
  const rpc = async (route, body, abort = signal) => {
    const response = await fetch(descriptor.url + '/api/duplex/haru/' + route, {
      method: body === undefined ? 'GET' : 'POST', signal: abort,
      headers: {'X-OpenCore-Hub-Secret': descriptor.secret, 'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw Error(result.error || 'Haru audio request failed.');
    return result;
  };
  const queue = new HaruAudioBuffer(), playback = new Set();
  let sessionId = crypto.randomUUID(), closed = false, timer, nextAudio = 0;
  const close = () => {
    if (closed) return;
    closed = true; clearTimeout(timer); signal.removeEventListener('abort', close);
    for (const source of playback) { source.onended = null; source.stop(); source.disconnect(); }
    playback.clear();
    if (sessionId) void rpc('stop', {sessionId}, null).catch(() => {});
  };
  const fail = error => { if (!closed) { close(); onError(String(error.message || error)); } };
  signal.addEventListener('abort', close, {once: true});
  const play = frame => {
    if (closed) return;
    if (frame.text) onTranscript('out', frame.text);
    const bytes = Uint8Array.from(atob(frame.audio), char => char.charCodeAt(0));
    if (bytes.length !== 6400) throw Error('Haru returned an invalid audio frame.');
    const buffer = context.createBuffer(1, haruSamples, haruRate), samples = buffer.getChannelData(0), pcm = new DataView(bytes.buffer);
    for (let i = 0; i < haruSamples; i++) samples[i] = pcm.getInt16(i * 2, true) / 32768;
    const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
    source.onended = () => { playback.delete(source); source.disconnect(); }; playback.add(source);
    const when = Math.max(context.currentTime + .025, nextAudio); source.start(when); nextAudio = when + buffer.duration;
    if (frame.done) fail(Error('Haru reached the ten-minute call limit. Start a new call to continue.'));
  };
  try {
    let state = await rpc('start', {id, reference, persona, sessionId});
    if (state.sessionId !== sessionId) throw Error('The audio service returned a different call identity.');
    if (closed || signal.aborted) { void rpc('stop', {sessionId}, null).catch(() => {}); signal.throwIfAborted(); }
    const deadline = Date.now() + 900000;
    while (state.status === 'loading') {
      if (Date.now() > deadline) throw Error('Haru model loading timed out.');
      await new Promise(resolve => setTimeout(resolve, 300)); signal.throwIfAborted();
      state = await rpc('status');
      if (state.sessionId !== sessionId && state.status !== 'failed') throw Error('The Haru call was stopped.');
    }
    if (state.status !== 'ready') throw Error(state.error || 'Haru could not start.');
    play(state.initial);
    const tick = async () => {
      if (closed) return;
      const started = performance.now(), frame = queue.take();
      try {
        if (frame) play(await rpc('frame', {sessionId, audio: encodeHaruFrame(frame)}));
      } catch (error) { if (!signal.aborted) fail(error); return; }
      if (!closed) timer = setTimeout(tick, Math.max(0, 1000 * haruSamples / haruRate - (performance.now() - started)));
    };
    timer = setTimeout(tick, 200);
    return {
      close,
      input(frame) { if (!closed) { try { queue.append(frame); } catch (error) { fail(error); } } },
      mute(value) { queue.mute(value); },
    };
  } catch (error) { close(); throw error; }
}
