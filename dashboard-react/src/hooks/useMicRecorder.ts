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

    // 1. Mic access. Surface a clear error on the dialog if the user denies
    //    or the browser refuses (e.g. non-HTTPS context).
    let stream: MediaStream;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("mic unavailable in this browser (needs HTTPS or localhost)");
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (e: unknown) {
      const name = (e as { name?: string })?.name || "";
      const msg =
        name === "NotAllowedError" ? "Mic permission denied — allow it in your browser." :
        name === "NotFoundError"   ? "No microphone found on this device." :
        e instanceof Error          ? e.message :
                                      "mic unavailable";
      console.error("[mic] getUserMedia failed:", e);
      setError(msg);
      setState("error");
      return;
    }
    streamRef.current = stream;

    // 2. Optional audio analysis graph (used for the meter + VAD). If the
    //    AudioContext fails for any reason, recording itself still works —
    //    we just lose VAD + the mic's voice-reactive visuals. Don't let
    //    audio-graph failure abort the whole recording.
    let analyser: AnalyserNode | null = null;
    try {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) throw new Error("AudioContext unsupported");
      const ctx = new Ctx();
      if (ctx.state === "suspended") await ctx.resume();
      const source = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.6;
      source.connect(analyser);
      ctxRef.current = ctx;
      analyserRef.current = analyser;
      vadBufferRef.current = new Uint8Array(analyser.fftSize);
    } catch (e) {
      console.warn("[mic] audio graph setup failed; recording will still work:", e);
      ctxRef.current = null;
      analyserRef.current = null;
      vadBufferRef.current = null;
    }

    // 3. MediaRecorder. If construction fails, fall through to error state.
    const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    let mimeType = "";
    for (const m of mimeCandidates) {
      if (MediaRecorder.isTypeSupported(m)) { mimeType = m; break; }
    }
    let recorder: MediaRecorder;
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "MediaRecorder unsupported";
      console.error("[mic] MediaRecorder failed:", e);
      setError(msg);
      setState("error");
      // Clean up the open audio graph + tracks so we don't leak.
      try { ctxRef.current?.close(); } catch { /* ignore */ }
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
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

    // 4. Elapsed-time timer (also enforces maxDuration as a hard upper bound).
    timerRef.current = setInterval(() => {
      const elapsed = (performance.now() - startedRef.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxDurationSec) stop();
    }, 100);

    // 5. Voice activity detection — silence-based auto-stop. Only runs if
    //    the analyser exists (audio graph setup succeeded) AND VAD is enabled.
    if (autoStopSilenceMs > 0 && analyser) {
      vadTimerRef.current = setInterval(() => {
        const a = analyserRef.current;
        const buf = vadBufferRef.current;
        if (!a || !buf) return;
        try {
          // Wrap in try/catch — calling on a closed/disconnected analyser
          // can throw `InvalidStateError`. Just skip that tick rather than
          // spamming the console or breaking the VAD loop.
          a.getByteTimeDomainData(buf as unknown as Uint8Array<ArrayBuffer>);
        } catch {
          return;
        }

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
          speechSeenRef.current = true;
          lastVoiceMsRef.current = now;
          return;
        }

        if (!speechSeenRef.current) return;
        if (lastVoiceMsRef.current === null) return;

        if (now - lastVoiceMsRef.current >= autoStopSilenceMs) {
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
