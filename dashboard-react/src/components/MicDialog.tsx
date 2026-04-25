import { useEffect, useRef, useState } from "react";
import { Mic, Square, X } from "lucide-react";
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
      analyser.getByteTimeDomainData(buf as unknown as Uint8Array<ArrayBuffer>);
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

export function MicDialog({ open, state, seconds, analyser, onStop, onCancel }: Props) {
  // Esc cancels
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  const listening = state === "listening";
  const voiceLevel = useVoiceLevel(analyser, open && listening);

  // Slow rotating hue while listening — the mic feels "alive" even before
  // the user speaks. When voice arrives, scale + glow track the level.
  const [hue, setHue] = useState(340); // start near coral (~340°)
  useEffect(() => {
    if (!open || !listening) return;
    const id = setInterval(() => setHue((h) => (h + 2) % 360), 80);
    return () => clearInterval(id);
  }, [open, listening]);

  const status = state === "transcribing" ? "Transcribing" : state === "error" ? "Error" : "Listening";
  const statusColor =
    state === "transcribing" ? "text-status-scheduled"
    : state === "error"      ? "text-status-overdue"
                             : "text-accent-coral";

  // Dynamic visuals for the mic button when listening:
  //  - hue rotates slowly through the accent palette
  //  - voiceLevel scales the button up (1.0 → 1.16) and fattens the glow
  //  - the rings ping faster + brighter the louder you are
  const baseColor = `hsl(${hue} 88% 64%)`;
  const accentColor = `hsl(${(hue + 60) % 360} 88% 68%)`;
  const buttonScale = 1 + voiceLevel * 0.16;
  const glowAlpha   = 0.35 + voiceLevel * 0.45;
  const ringSpeed1  = 3 - voiceLevel * 1.4;   // faster pings when louder
  const ringSpeed2  = 4 - voiceLevel * 1.8;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
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

          {/* Mic button — scales with voice level, hue rotates slowly */}
          <span
            className={cn(
              "relative z-[2] w-24 h-24 rounded-full grid place-items-center text-paper-50",
              "shadow-[0_8px_28px_rgba(0,0,0,0.35)]",
            )}
            style={{
              background: `linear-gradient(135deg, ${baseColor} 0%, ${accentColor} 100%)`,
              transform: `scale(${buttonScale.toFixed(3)})`,
              transition: "transform 60ms linear, background 200ms linear",
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
