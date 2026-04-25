import { useCallback, useRef, useState } from "react";

export type MicState = "idle" | "listening" | "transcribing" | "error";

interface UseMicOptions {
  maxDurationSec?: number;
  onTranscribed?: (resp: { ok: boolean; transcript?: string; mission?: string; decision?: string; error?: string }) => void;
  /** Override the default upload behaviour. Used by the donation prompt to
   *  POST to /missions/donate/confirm instead of /missions/auto/start-from-mic. */
  uploadFn?: (blob: Blob, mime: string) => Promise<{ ok: boolean; transcript?: string; decision?: string; error?: string }>;

  // === Voice activity detection ===
  /** Auto-stop after this many ms of silence (only AFTER the user has been
   *  detected speaking at least once). 0 / undefined = disable VAD, fall back
   *  to manual stop or maxDuration. Default 3000 ms. */
  autoStopSilenceMs?: number;
  /** RMS threshold (0..1) above which the live audio counts as "speech".
   *  Default 0.025 — comfortably above typical mic noise floor (~0.005-0.01). */
  silenceThreshold?: number;
  /** Wait at least this long after start() before VAD becomes active. Stops
   *  the recorder cutting itself off in the first 200-400 ms when the audio
   *  graph is still stabilising. Default 500 ms. */
  vadWarmupMs?: number;
}

export function useMicRecorder({
  maxDurationSec = 30,
  onTranscribed,
  uploadFn,
  autoStopSilenceMs = 3000,
  silenceThreshold = 0.025,
  vadWarmupMs = 500,
}: UseMicOptions = {}) {
  const [state, setState] = useState<MicState>("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const streamRef    = useRef<MediaStream | null>(null);
  const ctxRef       = useRef<AudioContext | null>(null);
  const analyserRef  = useRef<AnalyserNode | null>(null);
  const recorderRef  = useRef<MediaRecorder | null>(null);
  const chunksRef    = useRef<Blob[]>([]);
  const timerRef     = useRef<ReturnType<typeof setInterval> | null>(null);
  // VAD: separate poll loop (smaller cadence) so it never starves the elapsed-time timer.
  const vadTimerRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const vadBufferRef = useRef<Uint8Array | null>(null);
  const lastVoiceMsRef = useRef<number | null>(null);
  const speechSeenRef  = useRef(false);
  const startedRef   = useRef<number>(0);
  const cancelledRef = useRef(false);

  const cleanup = useCallback(() => {
    if (timerRef.current)    { clearInterval(timerRef.current);    timerRef.current = null; }
    if (vadTimerRef.current) { clearInterval(vadTimerRef.current); vadTimerRef.current = null; }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (ctxRef.current) { try { void ctxRef.current.close(); } catch { /* ignore */ } ctxRef.current = null; }
    analyserRef.current = null;
    recorderRef.current = null;
    vadBufferRef.current = null;
    lastVoiceMsRef.current = null;
    speechSeenRef.current = false;
  }, []);

  const stop = useCallback(() => {
    try {
      recorderRef.current && recorderRef.current.state !== "inactive" && recorderRef.current.stop();
    } catch { /* ignore */ }
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
    vadBufferRef.current = new Uint8Array(analyser.fftSize);

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
    lastVoiceMsRef.current = null;
    speechSeenRef.current = false;

    // Elapsed-time timer (also enforces maxDuration as a hard upper bound).
    timerRef.current = setInterval(() => {
      const elapsed = (performance.now() - startedRef.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxDurationSec) stop();
    }, 100);

    // Voice activity detection — silence-based auto-stop.
    if (autoStopSilenceMs > 0) {
      vadTimerRef.current = setInterval(() => {
        const a = analyserRef.current;
        const buf = vadBufferRef.current;
        if (!a || !buf) return;
        // Cast: getByteTimeDomainData wants `Uint8Array<ArrayBuffer>` specifically; our buffer
        // satisfies it but TS widens to `ArrayBufferLike` through the ref.
        a.getByteTimeDomainData(buf as unknown as Uint8Array<ArrayBuffer>);

        // RMS on a -1..1 normalised window.
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);

        const now = performance.now();
        const sinceStart = now - startedRef.current;

        // Skip the first vadWarmupMs while the audio graph stabilises.
        if (sinceStart < vadWarmupMs) return;

        if (rms > silenceThreshold) {
          // Voice detected — refresh the silence clock.
          speechSeenRef.current = true;
          lastVoiceMsRef.current = now;
          return;
        }

        // Below threshold = silence. Only count once we've actually heard
        // some speech, so a long pause before the user begins doesn't trigger.
        if (!speechSeenRef.current) return;
        if (lastVoiceMsRef.current === null) return;

        if (now - lastVoiceMsRef.current >= autoStopSilenceMs) {
          // Auto-stop. Clear the VAD timer immediately so we don't fire twice
          // while the recorder is winding down.
          if (vadTimerRef.current) {
            clearInterval(vadTimerRef.current);
            vadTimerRef.current = null;
          }
          stop();
        }
      }, 80);
    }
  }, [maxDurationSec, autoStopSilenceMs, silenceThreshold, vadWarmupMs, stop]);

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

    try {
      let j;
      if (uploadFn) {
        j = await uploadFn(blob, mime);
      } else {
        const fd = new FormData();
        fd.append("audio", blob, `recording.${ext}`);
        fd.append("seed_eur", "500");
        fd.append("wait_seconds", "60");
        const r = await fetch("/missions/auto/start-from-mic", { method: "POST", body: fd });
        j = await r.json();
      }
      onTranscribed?.(j);
      setState("idle");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "upload failed";
      setError(msg);
      setState("error");
    }
  }, [cleanup, onTranscribed, uploadFn]);

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
