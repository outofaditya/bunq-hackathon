import { useCallback, useRef, useState } from "react";

export type MicState = "idle" | "listening" | "transcribing" | "error";

interface UseMicOptions {
  maxDurationSec?: number;
  onTranscribed?: (resp: { ok: boolean; transcript?: string; mission?: string; error?: string }) => void;
}

export function useMicRecorder({ maxDurationSec = 30, onTranscribed }: UseMicOptions = {}) {
  const [state, setState] = useState<MicState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const streamRef   = useRef<MediaStream | null>(null);
  const ctxRef      = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef   = useRef<Blob[]>([]);
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedRef  = useRef<number>(0);
  const cancelledRef = useRef(false);

  const cleanup = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (ctxRef.current) { try { void ctxRef.current.close(); } catch { /* ignore */ } ctxRef.current = null; }
    analyserRef.current = null;
    recorderRef.current = null;
  }, []);

  const start = useCallback(async () => {
    setError(null);
    cancelledRef.current = false;
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "mic unavailable";
      setError(msg);
      setState("error");
      return;
    }
    streamRef.current = stream;

    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    if (ctx.state === "suspended") await ctx.resume();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(analyser);
    ctxRef.current = ctx;
    analyserRef.current = analyser;

    const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    let mimeType = "";
    for (const m of mimeCandidates) {
      if (MediaRecorder.isTypeSupported(m)) { mimeType = m; break; }
    }
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => void onStop();
    recorderRef.current = recorder;

    recorder.start(250);
    startedRef.current = performance.now();
    setSeconds(0);
    setState("listening");
    timerRef.current = setInterval(() => {
      const elapsed = (performance.now() - startedRef.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxDurationSec) stop();
    }, 100);
  }, [maxDurationSec]);

  const stop = useCallback(() => {
    try {
      recorderRef.current && recorderRef.current.state !== "inactive" && recorderRef.current.stop();
    } catch { /* ignore */ }
  }, []);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    try {
      recorderRef.current && recorderRef.current.state !== "inactive" && recorderRef.current.stop();
    } catch { /* ignore */ }
    cleanup();
    setState("idle");
  }, [cleanup]);

  const onStop = useCallback(async () => {
    if (cancelledRef.current) return;
    setState("transcribing");

    const mime = recorderRef.current?.mimeType || "audio/webm";
    const blob = new Blob(chunksRef.current, { type: mime });
    cleanup();

    const ext = mime.includes("webm")
      ? "webm"
      : mime.includes("mp4")
      ? "m4a"
      : mime.includes("ogg")
      ? "ogg"
      : "bin";

    const fd = new FormData();
    fd.append("audio", blob, `recording.${ext}`);
    fd.append("seed_eur", "500");
    fd.append("wait_seconds", "60");

    try {
      const r = await fetch("/missions/auto/start-from-mic", { method: "POST", body: fd });
      const j = await r.json();
      onTranscribed?.(j);
      setState("idle");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "upload failed";
      setError(msg);
      setState("error");
    }
  }, [cleanup, onTranscribed]);

  return {
    state,
    seconds,
    error,
    analyser: analyserRef,
    start,
    stop,
    cancel,
  };
}
