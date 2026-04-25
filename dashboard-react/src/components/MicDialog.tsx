import { useEffect, useRef, useState } from "react";
import { Mic, MicOff, Square, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { MicMeter } from "@/components/MicMeter";
import type { MicState } from "@/hooks/useMicRecorder";
import { cn, fmtTime } from "@/lib/utils";

interface Props {
  open: boolean;
  state: MicState;
  seconds: number;
  analyser: AnalyserNode | null;
  onStop: () => void;
  onCancel: () => void;
  /** When the recorder hit an error (mic permission denied, no device, etc.)
   *  this is the human-readable message. Shown verbatim in the error view. */
  error?: string | null;
  /** Optional retry action — shown next to "Close" when state==='error'. */
  onRetry?: () => void;
}

/** Polls the analyser at rAF cadence and returns a smoothed 0..1 voice
 *  level. Used to drive the mic's visual response while the user speaks. */
function useVoiceLevel(analyser: AnalyserNode | null, active: boolean): number {
  const [level, setLevel] = useState(0);
  const rafRef = useRef<number | null>(null);
  const smoothRef = useRef(0);

  useEffect(() => {
    if (!analyser || !active) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      smoothRef.current = 0;
      setLevel(0);
      return;
    }
    const buf = new Uint8Array(analyser.fftSize);
    const tick = () => {
      try {
        // The analyser may have been disconnected between frames if the
        // recorder cleaned up (e.g. silence auto-stop fired). Read defensively.
        analyser.getByteTimeDomainData(buf as unknown as Uint8Array<ArrayBuffer>);
      } catch {
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / buf.length);
      // Map roughly 0..0.4 RMS → 0..1 level, clamp; smooth with EMA so
      // spikes don't stutter the visuals.
      const target = Math.max(0, Math.min(1, rms * 4));
      smoothRef.current = smoothRef.current * 0.78 + target * 0.22;
      setLevel(smoothRef.current);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [analyser, active]);

  return level;
}

export function MicDialog({ open, state, seconds, analyser, onStop, onCancel, error, onRetry }: Props) {
  // Esc cancels
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  const listening = state === "listening";
  const voiceLevel = useVoiceLevel(analyser, open && listening);

  // Slow rotation through the bunq palette while listening — the mic feels
  // "alive" even before the user speaks. The colours come straight from the
  // Press Kit rainbow so it stays on-brand at every frame.
  const BUNQ_HUES = [
    { base: "#FF7819", accent: "#FAAA00" }, // Orange → Orange-deep
    { base: "#FAC800", accent: "#FF7819" }, // Yellow → Orange
    { base: "#34CC8D", accent: "#26C7C3" }, // Green → Turquoise
    { base: "#0080FF", accent: "#47BFFF" }, // Blue → Light blue
    { base: "#C834D6", accent: "#E33095" }, // Purple → Fuschia
    { base: "#E63223", accent: "#FF7819" }, // Red → Orange
  ] as const;
  const [hueIdx, setHueIdx] = useState(0);
  useEffect(() => {
    if (!open || !listening) return;
    const id = setInterval(() => setHueIdx((i) => (i + 1) % BUNQ_HUES.length), 1400);
    return () => clearInterval(id);
  }, [open, listening, BUNQ_HUES.length]);

  const status = state === "transcribing" ? "Transcribing" : state === "error" ? "Error" : "Listening";
  const statusColor =
    state === "transcribing" ? "text-bunq-blue"
    : state === "error"      ? "text-bunq-red"
                             : "text-bunq-orange";

  // Dynamic visuals for the mic button when listening:
  //  - colour cycles through the bunq palette
  //  - voiceLevel scales the button up (1.0 → 1.16) and fattens the glow
  //  - the rings ping faster + brighter the louder you are
  const { base: baseColor, accent: accentColor } = BUNQ_HUES[hueIdx];
  const buttonScale = 1 + voiceLevel * 0.16;
  const glowAlpha   = 0.35 + voiceLevel * 0.45;
  const ringSpeed1  = 3 - voiceLevel * 1.4;
  const ringSpeed2  = 4 - voiceLevel * 1.8;

  // Browser-specific recovery hint based on the actual user agent. Keeps the
  // "Error" view actionable instead of just saying "something broke".
  const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
  const isSafari = /Safari/.test(ua) && !/Chrome|CriOS|Chromium/.test(ua);
  const isChrome = /Chrome|Chromium|CriOS/.test(ua);
  const isFirefox = /Firefox/.test(ua);
  const recoveryHint = isSafari
    ? "Open Safari → Settings → Websites → Microphone, then set localhost:8000 to Allow."
    : isChrome
    ? "Click the lock icon (🔒) next to the URL → Site settings → Microphone → Allow, then reload."
    : isFirefox
    ? "Click the camera/mic icon in the URL bar → Allowed, then reload."
    : "Allow microphone access for this site in your browser settings, then reload.";

  if (state === "error") {
    return (
      <Dialog open={open} onOpenChange={(o: boolean) => { if (!o) onCancel(); }}>
        <DialogContent hideClose className="text-center max-w-md">
          <DialogTitle className="label-uc mb-4 text-bunq-red">Microphone unavailable</DialogTitle>
          <div className="relative w-20 h-20 mx-auto mb-4 grid place-items-center">
            <span className="absolute inset-0 rounded-full bg-bunq-red/15" />
            <MicOff className="w-9 h-9 text-bunq-red relative z-[2]" />
          </div>
          <div className="text-body text-foreground mb-3">
            {error || "We couldn't access your microphone."}
          </div>
          <p className="text-meta text-muted-foreground mb-5 leading-relaxed">{recoveryHint}</p>
          <div className="flex gap-3 justify-center">
            {onRetry && (
              <Button onClick={onRetry} className="rounded-full px-6">
                <Mic className="w-4 h-4" /> Try again
              </Button>
            )}
            <Button variant="outline" onClick={onCancel} className="rounded-full px-6">
              <X className="w-4 h-4" /> Close <span className="kbd ml-2">Esc</span>
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={(o: boolean) => { if (!o) onCancel(); }}>
      <DialogContent hideClose className="text-center">
        <DialogTitle className={cn("label-uc mb-6 transition-colors", statusColor)}>
          {status}
        </DialogTitle>

        <div className="relative w-36 h-36 mx-auto mb-6 grid place-items-center">
          {/* Outer ping ring — speed accelerates with voice */}
          <span
            className="absolute inset-0 -m-2 rounded-full border animate-ping"
            style={{
              borderColor: baseColor,
              opacity: 0.5,
              animationDuration: `${ringSpeed1.toFixed(2)}s`,
            }}
          />
          <span
            className="absolute inset-0 -m-1 rounded-full border animate-ping"
            style={{
              borderColor: accentColor,
              opacity: 0.4,
              animationDuration: `${ringSpeed2.toFixed(2)}s`,
              animationDelay: "0.6s",
            }}
          />

          {/* Soft halo behind the button — brightness tracks the voice */}
          <span
            className="absolute inset-0 -m-6 rounded-full blur-2xl pointer-events-none"
            style={{
              background: `radial-gradient(circle, ${baseColor}, transparent 70%)`,
              opacity: glowAlpha,
              transition: "opacity 80ms linear",
            }}
          />

          {/* Mic button — scales with voice level, colour cycles bunq palette */}
          <span
            className={cn(
              "relative z-[2] w-24 h-24 rounded-full grid place-items-center text-bunq-white",
              "shadow-[0_8px_28px_rgba(0,0,0,0.35)]",
            )}
            style={{
              background: `linear-gradient(135deg, ${baseColor} 0%, ${accentColor} 100%)`,
              transform: `scale(${buttonScale.toFixed(3)})`,
              transition: "transform 60ms linear, background 600ms cubic-bezier(0.2,0,0,1)",
              boxShadow:
                `0 8px 28px rgba(0,0,0,0.35), ` +
                `0 0 ${(20 + voiceLevel * 80).toFixed(0)}px ${(voiceLevel * 8).toFixed(0)}px ${baseColor}`,
            }}
          >
            <Mic className="w-9 h-9" />
          </span>
        </div>

        <MicMeter analyser={analyser} active={listening} />

        <div className="text-title tabular text-foreground mt-4 mb-5">{fmtTime(seconds)}</div>

        <div className="flex gap-3 justify-center mb-3">
          <Button onClick={onStop} disabled={!listening} className="rounded-full px-6">
            <Square className="w-4 h-4" /> Stop &amp; transcribe
          </Button>
          <Button variant="outline" onClick={onCancel} className="rounded-full px-6">
            <X className="w-4 h-4" /> Cancel <span className="kbd ml-2">Esc</span>
          </Button>
        </div>

        <p className="text-meta text-muted-foreground">
          Just talk. Auto-stops when you pause for 3 seconds.
        </p>
      </DialogContent>
    </Dialog>
  );
}
