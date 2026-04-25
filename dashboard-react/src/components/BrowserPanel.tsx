import { useEffect, useRef, useState } from "react";
import { Globe } from "lucide-react";
import { motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface BrowserPanelState {
  visible: boolean;
  /** legacy field — ignored */
  zoomed?: boolean;
  line: string;
  step: string;
  shotData: string | null;
  changing: boolean;
}

export function BrowserPanel({ state }: { state: BrowserPanelState }) {
  // Double-buffer the screenshot: render two <img> elements, fade between
  // them as new frames arrive. Avoids the flicker that comes from re-mounting
  // a single <motion.img> via a `key` change (briefly shows nothing while
  // remounting). With double-buffer we always have a fully-loaded image
  // visible; the new one only takes over once it's decoded.
  const [frontSrc, setFrontSrc] = useState<string | null>(null);
  const [backSrc,  setBackSrc]  = useState<string | null>(null);
  const [showBack, setShowBack] = useState(false);
  const lastDataRef = useRef<string | null>(null);

  useEffect(() => {
    if (!state.shotData) return;
    if (state.shotData === lastDataRef.current) return;
    lastDataRef.current = state.shotData;
    const url = `data:image/png;base64,${state.shotData}`;

    // First frame: just set the front image, no fade dance.
    if (!frontSrc) {
      setFrontSrc(url);
      return;
    }
    // Subsequent frames: write to the hidden buffer, then flip.
    if (showBack) {
      setFrontSrc(url);
      // give the new front a tick to decode before swapping
      requestAnimationFrame(() => setShowBack(false));
    } else {
      setBackSrc(url);
      requestAnimationFrame(() => setShowBack(true));
    }
    // shotData is the trigger; the others are stable refs across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.shotData]);

  if (!state.visible) return null;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
      className="h-full flex flex-col"
    >
      <Card className="h-full flex flex-col p-0 overflow-hidden">
        <div className="flex items-center gap-3 p-3 border-b border-border/70 shrink-0">
          <span className="w-7 h-7 rounded border border-border bg-secondary grid place-items-center">
            <Globe className="w-3.5 h-3.5 text-paper-400" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="label-uc text-muted-foreground">Browser agent</div>
            <div className="text-body text-foreground truncate">{state.line}</div>
          </div>
          <Badge variant="outline" className="font-mono tabular shrink-0">{state.step}</Badge>
        </div>
        <div
          className={cn(
            "flex-1 min-h-0 m-3 mt-2 rounded-md overflow-hidden bg-paper-950 border border-border/70",
            "relative",
          )}
        >
          {/* Both images live here; whichever is "active" gets full opacity.
              CSS transition makes the cross-fade smooth and remount-free. */}
          {frontSrc && (
            <img
              src={frontSrc}
              alt=""
              decoding="async"
              className="absolute inset-0 w-full h-full object-contain transition-opacity duration-[180ms] ease-out"
              style={{ opacity: showBack ? 0 : 1 }}
            />
          )}
          {backSrc && (
            <img
              src={backSrc}
              alt=""
              decoding="async"
              className="absolute inset-0 w-full h-full object-contain transition-opacity duration-[180ms] ease-out"
              style={{ opacity: showBack ? 1 : 0 }}
            />
          )}
          {!frontSrc && !backSrc && (
            <div className="absolute inset-0 grid place-items-center text-muted-foreground text-meta">
              Loading…
            </div>
          )}
        </div>
      </Card>
    </motion.div>
  );
}
