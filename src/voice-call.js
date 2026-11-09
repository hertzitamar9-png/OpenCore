import * as React from "react";
import { startSophonLive, sophonEndpoint } from "./sophon-live.js";
import { isDesktopChromeLaunch } from "./slash-menu.js";

const preferencesKey = "opencore.voice-call.v1";
const sampleRate = 16000;
let microphoneOwner;

export function voiceReply(entries, submissionId) {
  const user = entries.findIndex(entry => entry.kind === "message" && entry.role === "user" &&
    entry.metadata?.submissionId === submissionId);
  if (user < 0) return "";
  let answer = "";
  for (const entry of entries.slice(user + 1)) {
    if (entry.kind !== "message") continue;
    if (entry.role === "user") break;
    if (entry.role === "assistant" && entry.content?.trim() && !entry.metadata?.studioReceipt) answer = entry.content;
  }
  return answer.trim();
}

const bytesToBase64 = bytes => {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return btoa(binary);
};

function wav(frames) {
  const count = frames.reduce((total, frame) => total + frame.length, 0);
  const bytes = new Uint8Array(44 + count * 2);
  const header = new DataView(bytes.buffer);
  const label = (offset, text) => bytes.set(Uint8Array.from(text, char => char.charCodeAt(0)), offset);
  label(0, "RIFF"); header.setUint32(4, bytes.length - 8, true);
  label(8, "WAVEfmt "); header.setUint32(16, 16, true);
  header.setUint16(20, 1, true); header.setUint16(22, 1, true);
  header.setUint32(24, sampleRate, true); header.setUint32(28, sampleRate * 2, true);
  header.setUint16(32, 2, true); header.setUint16(34, 16, true);
  label(36, "data"); header.setUint32(40, count * 2, true);
  let offset = 44;
  for (const frame of frames) {
    for (const sample of frame) {
      header.setInt16(offset, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true);
      offset += 2;
    }
  }
  return bytesToBase64(bytes);
}

function collectUtterance(onSpeech) {
  let preRoll = [], frames = [], silence = 0, samples = 0, voiced = 0;
  const reset = () => { preRoll = []; frames = []; silence = samples = voiced = 0; };
  const send = () => {
    const utterance = voiced >= sampleRate / 10 ? frames : null;
    reset();
    if (utterance) onSpeech(wav(utterance));
  };
  return {
    reset, send,
    accept(input) {
      let energy = 0;
      for (const sample of input) energy += sample * sample;
      const talking = Math.sqrt(energy / input.length) >= .014;
      if (!frames.length && !talking) {
        preRoll = [...preRoll.slice(-1), new Float32Array(input)];
        return;
      }
      if (!frames.length) { frames = preRoll; preRoll = []; }
      frames.push(new Float32Array(input));
      samples += input.length;
      if (talking) { voiced += input.length; silence = 0; } else silence += input.length;
      if (silence >= sampleRate * .65 || samples >= sampleRate * 18) send();
    },
  };
}

async function openMicrophone(session, onAudio) {
  if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioContext) throw Error("Microphone access is unavailable here.");
  const context = new AudioContext({ sampleRate });
  session.audio = context;
  await context.resume();
  session.signal.throwIfAborted();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: {
    channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
  }});
  if (session.signal.aborted) {
    stream.getTracks().forEach(track => track.stop());
    session.signal.throwIfAborted();
  }
  session.stream = stream;
  stream.getAudioTracks().forEach(track => { track.enabled = !session.muted; });
  const source = context.createMediaStreamSource(stream);
  const sampler = context.createScriptProcessor(2048, 1, 1);
  const silence = context.createGain();
  silence.gain.value = 0;
  source.connect(sampler).connect(silence).connect(context.destination);
  sampler.onaudioprocess = event => {
    if (!session.muted && !session.signal.aborted) onAudio(event.inputBuffer.getChannelData(0));
  };
  session.disconnect = () => { sampler.onaudioprocess = null; sampler.disconnect(); source.disconnect(); silence.disconnect(); };
  return context;
}

function nativeVoiceList() {
  return globalThis.speechSynthesis?.getVoices().filter(voice => voice.localService) ?? [];
}

function speakDevice(text, uri, signal) {
  const synth = globalThis.speechSynthesis;
  const voices = nativeVoiceList();
  const voice = voices.find(item => item.voiceURI === uri) ?? voices.find(item => item.default) ?? voices[0];
  if (!voice) throw Error("Install a device voice or choose a speech synthesis model.");
  const parts = text.match(/[\s\S]{1,230}(?:\s|$)|[\s\S]{1,230}/g) ?? [];
  return parts.reduce((promise, part) => promise.then(() => new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const utterance = new SpeechSynthesisUtterance(part.trim());
    utterance.voice = voice;
    utterance.lang = voice.lang;
    const finish = error => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      error ? reject(error) : resolve();
    };
    const abort = () => { finish(signal.reason); synth.cancel(); };
    const timer = setTimeout(() => { finish(Error("The device voice did not finish.")); synth.cancel(); }, 45000);
    signal.addEventListener("abort", abort, { once: true });
    utterance.onend = () => finish();
    utterance.onerror = event => finish(Error("Device voice: " + event.error));
    synth.speak(utterance);
  })), Promise.resolve());
}

function playAudio(url, signal) {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const audio = new Audio(url);
    const finish = error => {
      audio.onended = audio.onerror = null;
      signal.removeEventListener("abort", abort);
      audio.pause();
      audio.removeAttribute("src");
      error ? reject(error) : resolve();
    };
    const abort = () => finish(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    audio.onended = () => finish();
    audio.onerror = () => finish(Error("Could not play the generated voice."));
    audio.play().catch(finish);
  });
}

const callRpc = (command, args = {}) => window.__TAURI_INTERNALS__.invoke(command, args);

export async function runVoiceChat(text, signal, { blocked, currentId, settings, run, cancel, computerUse }) {
  if (blocked) throw Error(blocked);
  signal.throwIfAborted();
  const id = crypto.randomUUID();
  const skills = [...settings.skills];
  if (!globalThis.__OPENCORE_PHONE__ && (computerUse || isDesktopChromeLaunch(text)) && !skills.includes("computer-use"))
    skills.push("computer-use");
  const stop = () => { const active = currentId(); if (active) void cancel(active).catch(() => {}); };
  let completed;
  signal.addEventListener("abort", stop, { once: true });
  try {
    completed = await run({ ...settings, id, text, skills, files: [], voice: true });
  } finally { signal.removeEventListener("abort", stop); }
  signal.throwIfAborted();
  if (!completed) throw Error("The text turn stopped. See the conversation for details.");
  const entries = await callRpc("get_conversation", { id: currentId() });
  const reply = voiceReply(entries, id);
  if (!reply) throw Error("The Brain finished without a spoken reply. Its activity is in the conversation.");
  return reply;
}
async function speakModel(text, modelId, voice, signal) {
  let job = await callRpc("submit_studio_job", { request: { modelId, prompt: text,
    settings: voice ? { voice } : {}, conversationId: null } });
  const id = job.id;
  const cancel = () => void callRpc("cancel_studio_job", { id }).catch(() => {});
  if (signal.aborted) { cancel(); signal.throwIfAborted(); }
  signal.addEventListener("abort", cancel, { once: true });
  try {
    const deadline = Date.now() + 180000;
    while (!["completed", "failed", "cancelled"].includes(job.status)) {
      if (Date.now() > deadline) { cancel(); throw Error("The speech synthesis model did not finish."); }
      await new Promise(resolve => setTimeout(resolve, 500));
      signal.throwIfAborted();
      job = (await callRpc("list_studio_jobs")).find(item => item.id === id);
      if (!job) throw Error("The speech synthesis job is no longer available.");
    }
    if (job.status !== "completed") throw Error(job.error || "The speech synthesis job stopped.");
    const path = job.outputs.find(value => /\.(wav|flac|mp3|opus|ogg)$/i.test(value));
    if (!path) throw Error("The speech model did not return audio.");
    const audio = await callRpc("studio_output_preview", { id, path });
    await playAudio(audio.dataUrl, signal);
  } finally { signal.removeEventListener("abort", cancel); }
}

function readPreferences() {
  try { return JSON.parse(localStorage.getItem(preferencesKey) || "{}") || {}; }
  catch { return {}; }
}

const stages = {
  opening: "Connecting…", listening: "Listening", transcribing: "Transcribing…", thinking: "Working in chat…",
  voice: "Preparing voice…", speaking: "Speaking", live: "Live audio", muted: "Microphone muted",
};

export function VoiceCall({ onClose, brainLabel, brainPicker, disabled, sendTurn }) {
  const [config, setConfig] = React.useState(readPreferences);
  const [models, setModels] = React.useState(null);
  const [speech, setSpeech] = React.useState(null);
  const [voices, setVoices] = React.useState(nativeVoiceList);
  const [stage, setStage] = React.useState("");
  const [error, setError] = React.useState("");
  const [muted, setMuted] = React.useState(false);
  const [turn, setTurn] = React.useState(null);
  const [transcript, setTranscript] = React.useState({ in: "", out: "" });
  const [delegation, setDelegation] = React.useState(null);
  const current = React.useRef(null);
  const latest = React.useRef(sendTurn);
  latest.current = sendTurn;
  const h = React.createElement;

  const setPreference = (name, value) => setConfig(previous => {
    const next = { ...previous, [name]: value };
    try { localStorage.setItem(preferencesKey, JSON.stringify(next)); } catch {}
    return next;
  });

  React.useEffect(() => {
    let open = true;
    const list = globalThis.__opencoreModelLibrary?.read?.(() => callRpc("list_model_library")) ?? callRpc("list_model_library");
    Promise.allSettled([list, callRpc("speech_status")]).then(([library, status]) => {
      if (!open) return;
      setModels(library.status === "fulfilled" ? library.value?.models ?? [] : []);
      if (status.status === "fulfilled") setSpeech(status.value);
    });
    const update = () => setVoices(nativeVoiceList());
    globalThis.speechSynthesis?.addEventListener("voiceschanged", update);
    return () => {
      open = false;
      globalThis.speechSynthesis?.removeEventListener("voiceschanged", update);
      current.current?.close();
    };
  }, []);

  const kind = config.kind === "sophon" ? "sophon" : "local";
  const speechModels = (models || []).filter(model => model.installed && model.category === "speech" &&
    ["phonon-2", "whisper-large-v3-turbo", "whisper-large-v3"].includes(model.id));
  if (speech?.installed && !speechModels.some(model => model.id === speech.modelId))
    speechModels.push({ id: speech.modelId, label: speech.modelId });
  const stt = speechModels.find(model => model.id === config.stt) ??
    speechModels.find(model => model.id === speech?.modelId) ?? speechModels[0];
  const ttsModels = (models || []).filter(model => model.category === "tts" && (model.runtimeConnected || model.installed && model.runtimeReady));
  const tts = ttsModels.find(model => model.id === config.tts)?.id ?? "device";
  const playable = tts !== "device" || voices.length > 0;

  const hangUp = () => {
    current.current?.close();
    current.current = null;
    onClose();
  };

  const start = async () => {
    if (current.current) return;
    setError("");
    if (microphoneOwner) { setError("Hang up the other OpenCore call first."); return; }
    if (kind === "local" && (!stt || !playable || disabled)) {
      setError(disabled || (!stt ? "Install a supported speech-to-text model in Models." : "Choose a speech synthesis model or install a device voice."));
      return;
    }
    let endpoint;
    try { if (kind === "sophon") endpoint = sophonEndpoint(config.endpoint); }
    catch (failure) { setError(failure.message); return; }
    const controller = new AbortController();
    const session = { signal: controller.signal, muted: false, ready: false };
    session.close = () => {
      if (session.signal.aborted) return;
      controller.abort();
      session.turn?.abort();
      session.sophon?.close();
      session.stream?.getTracks().forEach(track => track.stop());
      session.disconnect?.();
      void session.audio?.close().catch(() => {});
      if (microphoneOwner === session) microphoneOwner = null;
    };
    microphoneOwner = current.current = session;
    setStage("opening");
    setMuted(false);
    setTurn(null);
    setTranscript({ in: "", out: "" });
    setDelegation(null);
    const nextStage = phase => { if (!session.signal.aborted) setStage(phase); };
    const say = async audio => {
      if (!session.ready || session.turn || session.muted || session.signal.aborted) return;
      const utterance = new AbortController();
      const signal = utterance.signal;
      session.turn = utterance;
      setTurn(utterance);
      try {
        setError("");
        nextStage("transcribing");
        const sessionId = crypto.randomUUID();
        const cancel = () => void callRpc("speech_cancel", { sessionId }).catch(() => {});
        signal.addEventListener("abort", cancel, { once: true });
        let text;
        try {
          const started = await callRpc("speech_start", { sessionId });
          if (signal.aborted) { cancel(); signal.throwIfAborted(); }
          text = (await callRpc("speech_transcribe", { sessionId: started, audio })).text?.trim();
          signal.throwIfAborted();
        } finally {
          signal.removeEventListener("abort", cancel);
          cancel();
        }
        if (!text) return;
        setTranscript({ in: text, out: "" });
        nextStage("thinking");
        const answer = await latest.current(text, signal, Boolean(config.computerUse));
        signal.throwIfAborted();
        const spoken = answer.replace(/<think>[\s\S]*?<\/think>/gi, "").replace(/<\/?think>/gi, "").replace(/[`]{3}[\s\S]*?[`]{3}/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").trim();
        if (!spoken) return;
        setTranscript({ in: text, out: answer });
        nextStage(tts === "device" ? "speaking" : "voice");
        if (tts === "device") await speakDevice(spoken, config.voice, signal);
        else await speakModel(spoken, tts, config.speaker?.trim(), signal);
      } catch (failure) {
        if (!signal.aborted) setError(String(failure.message || failure));
      } finally {
        if (!session.signal.aborted) {
          session.turn = null;
          setTurn(null);
          nextStage("listening");
        }
      }
    };
    session.collect = collectUtterance(say);
    try {
      const context = await openMicrophone(session, frame => {
        if (kind === "sophon") session.sophon?.input(frame);
        else if (session.ready && !session.turn) session.collect.accept(frame);
      });
      session.signal.throwIfAborted();
      if (kind === "sophon") {
        session.sophon = await startSophonLive({
          endpoint, context, signal: session.signal, voice: config.sophonVoice?.trim(),
          onTranscript: (source, delta) => setTranscript(previous => ({ ...previous, [source]: (previous[source] + delta).slice(-4000) })),
          onDelegation: request => {
            if (session.pending || session.turn) {
              session.sophon?.answer(request.id, "OpenCore is already handling a task.");
              return;
            }
            session.pending = request;
            setDelegation(request);
          },
          onError: reason => { setError(reason); session.close(); current.current = null; setStage(""); setDelegation(null); },
        });
        nextStage("live");
      } else {
        if (speech?.modelId !== stt.id) await callRpc("speech_set_model", { modelId: stt.id });
        session.signal.throwIfAborted();
        if (!speech?.enabled) await callRpc("speech_set_enabled", { enabled: true });
        session.signal.throwIfAborted();
        session.ready = true;
        nextStage("listening");
      }
    } catch (failure) {
      if (!session.signal.aborted) {
        setError(String(failure.message || failure));
        session.close();
        current.current = null;
        setStage("");
      }
    }
  };

  const toggleMute = () => {
    const session = current.current;
    if (!session) return;
    session.muted = !session.muted;
    session.collect.reset();
    session.stream?.getAudioTracks().forEach(track => { track.enabled = !session.muted; });
    setMuted(session.muted);
  };

  const decideDelegation = async approve => {
    const session = current.current;
    if (!session?.sophon || !delegation || session.turn) return;
    const request = delegation;
    const task = new AbortController();
    session.turn = task;
    session.pending = null;
    setDelegation(null);
    try {
      const answer = approve ? await latest.current(request.task, task.signal, Boolean(config.computerUse)) : "The user declined the requested action.";
      if (!session.signal.aborted) session.sophon.answer(request.id, answer);
    } catch (failure) {
      if (!session.signal.aborted) {
        session.sophon.answer(request.id, "OpenCore could not complete the requested action.");
        setError(String(failure.message || failure));
      }
    } finally { session.turn = null; }
  };

  const field = (label, control) => h("label", null, h("span", null, label), React.cloneElement(control, { "aria-label": label }));
  const select = (key, value, options) => h("select", {
    value, onChange: event => setPreference(key, event.currentTarget.value),
  }, options.map(([id, title]) => h("option", { value: id, key: id }, title)));
  const input = (key, placeholder) => h("input", {
    value: config[key] ?? "", placeholder, onChange: event => setPreference(key, event.currentTarget.value),
    maxLength: key === "endpoint" ? 512 : 100, autoComplete: "off", spellCheck: false,
  });

  return h("section", { className: "voice-call", "aria-label": "Voice call" },
    h("div", { className: "voice-call-head" },
      h("div", null, h("strong", null, kind === "sophon" ? "Sophon Realtime" : "Voice call"),
        h("span", { role: "status" }, stage ? muted && ["listening", "live"].includes(stage) ? stages.muted : stages[stage] : brainLabel)),
      h("div", { className: "voice-call-actions" },
        stage && h("button", { type: "button", "aria-pressed": muted, onClick: toggleMute }, muted ? "Unmute" : "Mute"),
        turn && h("button", { type: "button", onClick: () => turn.abort() }, "Interrupt"),
        stage === "listening" && !muted && h("button", { type: "button", onClick: () => current.current?.collect.send() }, "Send speech"),
        h("button", { type: "button", className: stage ? "voice-hangup" : "", onClick: hangUp }, stage ? "Hang up" : "Close"))),
    error && h("p", { role: "alert", className: "voice-call-error" }, error),
    !stage && h(React.Fragment, null,
      h("div", { className: "voice-call-config" },
        field("Call mode", select("kind", kind, [["local", "Speech + Brain"], ["sophon", "Sophon Realtime v1"]])),
        kind === "local" ? h(React.Fragment, null,
          field("Speech-to-text", select("stt", stt?.id ?? "", speechModels.length ?
            speechModels.map(model => [model.id, model.label]) : [["", models ? "Install a speech model in Models" : "Checking installed speech models…"]])),
          field("Text-to-speech", select("tts", tts, [["device", "Device voice · low resource"], ...ttsModels.map(model => [model.id, model.label])])),
          tts === "device" ? field("Voice", select("voice", voices.some(voice => voice.voiceURI === config.voice) ? config.voice : "",
            [["", "Device default"], ...voices.map(voice => [voice.voiceURI, voice.name + " · " + voice.lang])]))
            : field("Voice / speaker ID", input("speaker", "Model default")))
          : h(React.Fragment, null,
            field("Sophon Live WebSocket", input("endpoint", "ws://127.0.0.1:8780/v1/live/sessions")),
            field("Voice", input("sophonVoice", "Server default")))),
      h("details", { className: "voice-brain" }, h("summary", null, "Brain · " + brainLabel), brainPicker),
      !globalThis.__OPENCORE_PHONE__ && h("label", { className: "voice-tool" },
        h("input", { type: "checkbox", checked: Boolean(config.computerUse),
          onChange: event => setPreference("computerUse", event.currentTarget.checked) }),
        "Enable Computer use for voice turns"),
      kind === "sophon" && h("p", { className: "voice-call-note" },
        "This mode connects to the publisher’s Sophon Live gateway through a local port. Your microphone audio goes to that server. You approve each task it hands to OpenCore. ",
        h("a", { href: "https://github.com/adventists-ai/sophon-realtime/blob/main/live/README.md", target: "_blank", rel: "noreferrer" }, "adventists-ai/Sophon-Realtime-v1 · gateway setup")),
      kind === "local" && (!stt || !playable || !speech?.enabled) && h("p", { className: "voice-call-note" },
        "Starting the call uses the selected speech model, Brain, voice, and chat approvals. It enables speech-to-text in Models if needed."),
      h("button", { type: "button", className: "voice-start", disabled: kind === "local" && !models, onClick: () => void start() }, "Start call")),
    stage && (transcript.in || transcript.out) && h("div", { className: "voice-call-transcript" },
      transcript.in && h("span", null, h("b", null, "You "), transcript.in),
      transcript.out && h("span", null, h("b", null, kind === "sophon" ? "Sophon " : "OpenCore "), transcript.out)),
    stage && delegation && h("div", { className: "voice-call-delegation" }, h("strong", null, "Sophon requests a task"),
      h("p", null, delegation.task),
      h("button", { type: "button", onClick: () => void decideDelegation(true) }, "Run in OpenCore"),
      h("button", { type: "button", onClick: () => void decideDelegation(false) }, "Decline")));
}
