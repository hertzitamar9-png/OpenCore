import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Mic } from "lucide-react";
import * as api from "./api";

export function SpeechButton({ onTranscript, onError }: { onTranscript: (text: string) => void; onError: (message: string) => void }) {
  const [phase, setPhase] = useState<"idle" | "starting" | "recording" | "transcribing">("idle");
  const recordingRequested = useRef(false);
  const alive = useRef(true);
  const busy = useRef(false);
  const recorder = useRef<MediaRecorder | undefined>(undefined);
  const stream = useRef<MediaStream | undefined>(undefined);
  const session = useRef<Promise<string> | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancelled = useRef(false);
  const [level, setLevel] = useState(0);
  const meter = useRef<AudioContext | undefined>(undefined);
  const frame = useRef(0);
  const button = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const releaseTracks = () => {
    stream.current?.getTracks().forEach((track) => track.stop()); stream.current = undefined; clearTimeout(timer.current);
    cancelAnimationFrame(frame.current); void meter.current?.close().catch(() => {}); meter.current = undefined;
    if (alive.current) setLevel(0);
  };
  const sleep = async () => { if (session.current) { try { await api.speechCancel(await session.current); } catch { /* Start failure is reported separately. */ } } session.current = undefined; };
  const finish = (cancel = false) => {
    recordingRequested.current = false;
    cancelled.current ||= cancel;
    if (recorder.current?.state === "recording") recorder.current.stop();
    else if (!recorder.current || cancel) releaseTracks();
    if (cancel) void sleep();
  };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; finish(true); };
  }, []);

  const start = async () => {
    if (busy.current) return;
    busy.current = true; recordingRequested.current = true; cancelled.current = false; setPhase("starting");
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("Microphone recording is unavailable in this window.");
      const audio = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = audio;
      if (!recordingRequested.current || !alive.current) { releaseTracks(); busy.current = false; if (alive.current) setPhase("idle"); return; }
      session.current = api.speechStart();
      const sessionId = await session.current;
      if (!recordingRequested.current || !alive.current || cancelled.current) {
        await api.speechCancel(sessionId);
        releaseTracks(); busy.current = false; if (alive.current) setPhase("idle"); return;
      }
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported?.(type));
      const capture = new MediaRecorder(audio, mimeType ? { mimeType } : undefined);
      recorder.current = capture;
      capture.onerror = () => { if (alive.current) onError("Microphone recording failed. Please try again."); finish(true); };
      const chunks: Blob[] = [];
      capture.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      capture.onstop = async () => {
        releaseTracks();
        try {
          if (cancelled.current || !alive.current || !session.current) return;
          const blob = new Blob(chunks, { type: capture.mimeType });
          if (blob.size < 1024) throw new Error('Click the microphone to start, speak, then click again to stop. No usable audio was recorded.');
          setPhase("transcribing");
          const id = await session.current;
          if (cancelled.current || !alive.current) return;
          const encoded = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(new Error("Could not read recorded audio"));
            reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
            reader.readAsDataURL(blob);
          });
          const result = await api.speechTranscribe(id, encoded);
          if (alive.current && !cancelled.current && result.text.trim()) onTranscript(result.text.trim());
          else if (alive.current && !cancelled.current) onError('No speech detected. Click the microphone to start, speak, then click again to stop.');
        } catch (error) { if (alive.current && !cancelled.current) onError(String(error)); }
        finally {
          await sleep(); busy.current = false; recorder.current = undefined;
          if (alive.current) {
            setPhase("idle");
            // Do not steal focus if the user moved to another field while transcribing.
            if (document.activeElement === button.current || document.activeElement === document.body)
              returnFocus.current?.focus({ preventScroll: true });
          }
        }
      };
      capture.start(200); setPhase("recording");
      if (typeof AudioContext !== 'undefined') {
        const context = new AudioContext(); meter.current = context;
        const analyser = context.createAnalyser(); analyser.fftSize = 512;
        context.createMediaStreamSource(audio).connect(analyser);
        void context.resume();
        const samples = new Float32Array(analyser.fftSize);
        let smooth = 0;
        const update = () => {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
          const target = Math.max(0, Math.min(1, (20 * Math.log10(Math.max(rms, 0.00001)) + 55) / 45));
          smooth += (target - smooth) * (target > smooth ? .65 : .18);
          if (alive.current) setLevel(smooth);
          frame.current = requestAnimationFrame(update);
        };
        update();
      }
      timer.current = setTimeout(() => finish(), 180_000);
    } catch (error) {
      releaseTracks(); await sleep(); busy.current = false;
      if (alive.current) { setPhase("idle"); onError(String(error)); }
    }
  };
  const label = phase === "idle" ? "Microphone: click to dictate" : phase === "starting" ? "Loading Microphone… click again to cancel" : phase === "recording" ? "Recording — click to stop" : "Transcribing — GPU memory is released when finished";
  return <span className="speech-control"><button ref={button} type="button" className={`speech-button ${phase}`} title={label} aria-label={label} aria-pressed={phase === 'recording'}
    disabled={phase === "transcribing"}
    onPointerDown={(event) => { if (event.button === 0) event.preventDefault(); }}
    onClick={() => { if (recordingRequested.current) finish(); else void start(); }}
    onKeyDown={(event) => { if (event.key === "Escape") finish(true); }}>
    <span className="speech-icon" aria-hidden="true"><Mic size={22} />
      {phase === 'recording' && <span className="speech-level" style={{ clipPath: `inset(${(1 - level) * 100}% 0 0 0)` }}><Mic size={22} /></span>}
    </span>
    {(phase === 'starting' || phase === 'transcribing') && <LoaderCircle size={12} className="speech-spinner" />}
  </button>{phase !== 'idle' && <span className="speech-status" role="status">{phase === 'recording' ? level > .12 ? 'Listening' : 'Listening · quiet' : phase === 'starting' ? 'Loading Microphone' : 'Transcribing'}</span>}</span>;
}
