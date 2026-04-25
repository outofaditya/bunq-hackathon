import { useEffect } from "react";
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

export function MicDialog({ open, state, seconds, analyser, onStop, onCancel }: Props) {
  // Esc cancels
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  const status = state === "transcribing" ? "Transcribing" : state === "error" ? "Error" : "Listening";
  const statusColor =
    state === "transcribing"
      ? "text-status-scheduled"
      : state === "error"
      ? "text-status-overdue"
      : "text-status-complete";

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <DialogContent hideClose className="text-center">
        <DialogTitle className={cn("label-uc mb-6", statusColor)}>{status}</DialogTitle>

        <div className="relative w-32 h-32 mx-auto mb-6 grid place-items-center">
          <span className="absolute inset-0 rounded-full border border-status-complete/60 animate-ping opacity-60" />
          <span className="absolute inset-0 rounded-full border border-status-complete/40 animate-ping opacity-30" style={{ animationDelay: "0.6s" }} />
          <span className={cn(
            "relative z-[2] w-20 h-20 rounded-full grid place-items-center text-primary-foreground shadow-[0_8px_24px_rgba(91,143,110,0.35)]",
            "bg-status-complete"
          )}>
            <Mic className="w-7 h-7" />
          </span>
        </div>

        <MicMeter analyser={analyser} active={state === "listening"} />

        <div className="text-title tabular text-foreground mt-4 mb-5">{fmtTime(seconds)}</div>

        <div className="flex gap-3 justify-center mb-3">
          <Button onClick={onStop} disabled={state !== "listening"} className="bg-status-complete hover:bg-status-complete/90 rounded-full px-6">
            <Square className="w-4 h-4" /> Stop &amp; transcribe
          </Button>
          <Button variant="outline" onClick={onCancel} className="rounded-full px-6">
            <X className="w-4 h-4" /> Cancel <span className="kbd ml-2">Esc</span>
          </Button>
        </div>

        <p className="text-meta text-muted-foreground">
          Speak the mission. Auto-stops after 30 seconds.
        </p>
      </DialogContent>
    </Dialog>
  );
}
