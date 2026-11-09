const rate = 16000;
export function sophonEndpoint(value) {
  let endpoint;
  try { endpoint = new URL(value?.trim() || "ws://127.0.0.1:8780/v1/live/sessions"); }
  catch { throw Error("Enter the Sophon Live WebSocket address."); }
  if (endpoint.hostname !== "127.0.0.1" || !["ws:", "wss:"].includes(endpoint.protocol) ||
      endpoint.pathname !== "/v1/live/sessions" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash)
    throw Error("Use a loopback WebSocket ending in /v1/live/sessions. Forward a remote Sophon server to a local port.");
  return endpoint.toString();
}

function encodePcm(samples) {
  const pcm = new DataView(new ArrayBuffer(samples.length * 2));
  for (let i = 0; i < samples.length; i++) pcm.setInt16(i * 2, Math.round(Math.max(-1, Math.min(1, samples[i])) * 32767), true);
  return btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)));
}

export function startSophonLive({ endpoint, context, signal, voice, onTranscript, onDelegation, onError }) {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(endpoint);
    const playback = new Set();
    let nextAudio = 0, started = false, closed = false;
    const send = event => { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(event)); };
    const clearPlayback = () => {
      for (const source of playback) { source.onended = null; source.stop(); source.disconnect(); }
      playback.clear();
    };
    const close = () => {
      if (closed) return;
      closed = true;
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      send({ type: "session.close" });
      if (socket.readyState < WebSocket.CLOSING) socket.close();
      clearPlayback();
    };
    const fail = message => {
      if (closed) return;
      close();
      if (started) onError(message); else reject(Error(message));
    };
    const abort = () => { close(); if (!started) reject(signal.reason); };
    const timer = setTimeout(() => fail("Sophon did not open a Live session within 15 seconds."), 15000);
    signal.addEventListener("abort", abort, { once: true });

    const play = encoded => {
      if (typeof encoded !== "string" || encoded.length > 24000) return;
      const pcm = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
      if (!pcm.length || pcm.length % 2) return;
      const audio = context.createBuffer(1, pcm.length / 2, rate), channel = audio.getChannelData(0);
      const data = new DataView(pcm.buffer);
      for (let i = 0; i < channel.length; i++) channel[i] = data.getInt16(i * 2, true) / 32768;
      if (nextAudio > context.currentTime + .65) { clearPlayback(); nextAudio = 0; }
      const source = context.createBufferSource();
      source.buffer = audio;
      source.connect(context.destination);
      source.onended = () => { playback.delete(source); source.disconnect(); };
      playback.add(source);
      const time = Math.max(nextAudio, context.currentTime + .025);
      source.start(time);
      nextAudio = time + audio.duration;
    };

    socket.onopen = () => send({
      type: "session.start",
      session: {
        model: "gpt-live-1",
        audio: { format: { type: "audio/pcm", rate }, output: voice ? { voice } : {} },
        delegation: { type: "client" },
        store: false,
      },
    });
    socket.onmessage = event => {
      if (closed) return;
      try {
        if (typeof event.data !== "string" || event.data.length > 64000) throw Error("Unexpected Sophon Live message.");
        const message = JSON.parse(event.data);
        if (message.type === "error") { fail(String(message.error?.message ?? "Sophon Live reported an error.").slice(0, 400)); return; }
        if (message.type === "session.started" && !started) {
          started = true;
          clearTimeout(timer);
          resolve({
            close,
            input: frame => {
              if (socket.bufferedAmount < rate) send({ type: "session.input_audio.append", audio: encodePcm(frame) });
            },
            answer: (id, content) => send({ type: "session.commentary.append", delegation_id: id, content }),
          });
          return;
        }
        if (!started) return;
        if (message.type === "session.closed") { fail("Sophon ended the call."); return; }
        if (message.type === "session.output_audio.delta") play(message.delta);
        if (message.type === "session.output_transcript.delta" || message.type === "session.input_transcript.delta") {
          if (typeof message.delta === "string") onTranscript(message.type.includes(".input_") ? "in" : "out", message.delta);
        }
        if (message.type === "session.delegation.created") {
          const { id, task } = message.delegation ?? {};
          if (typeof id === "string" && id.length <= 128 && typeof task === "string" && task.trim())
            onDelegation({ id, task: task.trim().slice(0, 4000) });
        }
      } catch (failure) { fail(String(failure.message || failure)); }
    };
    socket.onerror = () => fail("Could not connect to Sophon Live. Check the local port and the gateway’s allowed browser origins.");
    socket.onclose = () => fail("The connection to Sophon Live closed.");
  });
}
