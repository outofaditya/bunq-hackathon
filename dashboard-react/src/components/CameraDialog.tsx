import { useEffect, useRef, useState } from "react";
import { Camera, ScanLine, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  /** Called with the captured JPEG blob. The dialog stays open until the
   *  parent closes it — typically after the upload returns. */
  onCapture: (blob: Blob) => void;
  onCancel: () => void;
  status?: "idle" | "scanning" | "scanned" | "error";
  /** Optional message shown over the preview while scanning. */
  message?: string | null;
}

export function CameraDialog({ open, onCapture, onCancel, status = "idle", message }: Props) {
  const videoRef  = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Esc cancels
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  // Open / close the camera stream as the dialog visibility flips.
  useEffect(() => {
    if (!open) {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      return;
    }
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: "environment" },
            width:  { ideal: 1920 },
            height: { ideal: 1080 },
          },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Camera unavailable";
        setError(msg);
      }
    })();
    return () => { cancelled = true; };
  }, [open]);

  const capture = async () => {
    if (!videoRef.current || !canvasRef.current) return;
    if (busy || status === "scanning") return;
    const v = videoRef.current;
    const c = canvasRef.current;
    if (v.videoWidth === 0 || v.videoHeight === 0) return;
    setBusy(true);
    c.width  = v.videoWidth;
    c.height = v.videoHeight;
    const ctx = c.getContext("2d");
    if (ctx) ctx.drawImage(v, 0, 0, c.width, c.height);
    const blob: Blob | null = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.85));
    if (blob) onCapture(blob);
    setBusy(false);
  };

  const scanning = status === "scanning";
  const headline =
    error           ? "Camera error"
    : scanning      ? "Reading the invoice…"
    : status === "scanned" ? "Got it"
    : "Aim at the invoice";

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }}>
      <DialogContent hideClose className="max-w-2xl p-0 overflow-hidden">
        <DialogTitle className="sr-only">Receipt scanner</DialogTitle>

        <div className="relative bg-paper-950 aspect-video w-full">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover"
          />
          <canvas ref={canvasRef} className="hidden" />

          {/* Scan overlay frame */}
          <div className="pointer-events-none absolute inset-6 border-2 border-dashed border-white/40 rounded-md" />

          {/* Animated scan line while extracting */}
          {scanning && (
            <div className="pointer-events-none absolute inset-x-6 top-6 bottom-6 overflow-hidden rounded-md">
              <div
                className="absolute left-0 right-0 h-[2px] bg-status-scheduled shadow-[0_0_18px_4px_rgba(74,90,122,0.65)]"
                style={{ animation: "scanlinePct 1.6s ease-in-out infinite" }}
              />
            </div>
          )}

          {/* Status pill */}
          <div className="absolute top-4 left-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-paper-900/80 backdrop-blur text-paper-50 text-meta">
            <ScanLine className="w-3.5 h-3.5" />
            <span className="label-uc">{headline}</span>
          </div>

          {/* Inline message (e.g. "Couldn't read the IBAN") */}
          {message && (
            <div className="absolute bottom-4 left-4 right-4 px-3 py-2 rounded-md bg-paper-900/85 backdrop-blur text-paper-50 text-meta">
              {message}
            </div>
          )}

          {error && (
            <div className="absolute inset-0 grid place-items-center bg-paper-950/80 text-paper-50 px-6 text-center">
              <div>
                <div className="label-uc text-status-overdue mb-2">Camera error</div>
                <div className="text-body">{error}</div>
                <div className="text-meta text-paper-400 mt-3">Allow camera access in your browser, then re-open this dialog.</div>
              </div>
            </div>
          )}
        </div>

        <div className="flex gap-3 justify-center px-6 py-4 bg-card border-t border-border/70">
          <Button
            onClick={capture}
            disabled={!!error || busy || scanning}
            className="bg-status-scheduled hover:bg-status-scheduled/90 rounded-full px-6"
          >
            <Camera className="w-4 h-4" /> Capture
          </Button>
          <Button variant="outline" onClick={onCancel} className="rounded-full px-6">
            <X className="w-4 h-4" /> Close <span className="kbd ml-2">Esc</span>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
