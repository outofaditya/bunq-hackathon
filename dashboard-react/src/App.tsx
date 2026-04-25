import { useCallback, useEffect, useState } from "react";
import { Sparkles, Wifi, WifiOff } from "lucide-react";
import { MotionConfig } from "framer-motion";
import { TripView } from "@/components/TripView";
import { useEventBus } from "@/hooks/useEventBus";
import { cn } from "@/lib/utils";
import type { BusEvent, HealthInfo } from "@/lib/types";

const PUNCTUAL_EASE = [0.16, 1, 0.3, 1] as const;

export function App() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [tripEvent, setTripEvent] = useState<BusEvent | null>(null);

  const handleEvent = useCallback((ev: BusEvent) => {
    setTripEvent(ev);
  }, []);

  const status = useEventBus(handleEvent);

  useEffect(() => {
    let cancelled = false;
    fetch("/health")
      .then((r) => r.json())
      .then((j: HealthInfo) => { if (!cancelled) setHealth(j); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const reset = useCallback(() => {
    fetch("/chat/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionStorage.getItem("trip-session-id") || "default" }),
    }).catch(() => {});
    sessionStorage.removeItem("trip-session-id");
    // Force the TripView to reinitialise.
    window.location.reload();
  }, []);

  return (
    <MotionConfig transition={{ duration: 0.32, ease: PUNCTUAL_EASE }}>
      <div className="h-dvh grid grid-rows-[56px_1fr] bg-background text-foreground overflow-hidden">

        {/* Header */}
        <header className="flex items-center gap-3 px-6 border-b border-border/70 bg-background/80 backdrop-blur">
          <span className="w-6 h-6 rounded-md bg-punctual/15 grid place-items-center">
            <Sparkles className="w-3.5 h-3.5 text-punctual" />
          </span>
          <span className="text-foreground font-medium tracking-tight">Trip Agent</span>
          <span className="text-meta text-muted-foreground">— bunq</span>
          <div className="flex-1" />

          {health?.user_id && (
            <span className="text-meta text-muted-foreground tabular">
              user · <span className="text-foreground">{health.user_id}</span>
            </span>
          )}

          <div className={cn(
            "flex items-center gap-2 px-3 py-1.5 rounded-full",
            status === "live" ? "text-status-complete" : "text-muted-foreground",
          )}>
            {status === "live" ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            <span className="label-uc">{status === "live" ? "Live" : status === "reconnecting" ? "Reconnecting" : "Connecting"}</span>
          </div>
        </header>

        {/* Body */}
        <main className="min-h-0 overflow-hidden p-4">
          <TripView event={tripEvent} onExit={reset} />
        </main>
      </div>
    </MotionConfig>
  );
}
