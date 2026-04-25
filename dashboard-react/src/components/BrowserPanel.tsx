import { useEffect, useState } from "react";
import { Globe } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export interface BrowserPanelState {
  visible: boolean;
  zoomed: boolean;
  line: string;
  step: string;
  shotData: string | null;   // base64 PNG
  changing: boolean;
}

interface Props {
  state: BrowserPanelState;
}

export function BrowserPanel({ state }: Props) {
  const [animKey, setAnimKey] = useState(0);
  // Force a key bump on shot change for tw-animate fade transitions.
  useEffect(() => { setAnimKey((k) => k + 1); }, [state.shotData]);

  if (!state.visible) return null;

  return (
    <>
      {state.zoomed && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-40 animate-in fade-in duration-default ease-out" />
      )}
      <Card
        className={cn(
          "transition-[transform,box-shadow,border-radius] duration-slow ease-out origin-top",
          state.zoomed
            ? "fixed inset-[4vh_5vw] z-50 shadow-[0_60px_120px_rgba(0,0,0,0.6),0_0_0_1px_var(--color-border)]"
            : "relative",
        )}
      >
        <div className="flex items-center gap-3 p-4 border-b border-border">
          <span className="w-7 h-7 rounded border border-border bg-secondary grid place-items-center">
            <Globe className="w-3.5 h-3.5 text-paper-400" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="label-uc text-muted-foreground">Browser agent</div>
            <div className="text-body text-foreground truncate">{state.line}</div>
          </div>
          <Badge variant="outline" className="font-mono tabular">{state.step}</Badge>
        </div>
        <div
          className={cn(
            "rounded-md overflow-hidden bg-paper-950 border border-border m-4 mt-3",
            state.zoomed ? "flex-1 min-h-0" : "aspect-[1100/760] min-h-[220px]",
            "grid place-items-center",
          )}
        >
          {state.shotData ? (
            <img
              key={animKey}
              src={`data:image/png;base64,${state.shotData}`}
              className={cn(
                "w-full h-full object-contain",
                "animate-in fade-in duration-default ease-out",
                state.changing && "opacity-70"
              )}
              alt="browser agent screenshot"
            />
          ) : (
            <div className="text-muted-foreground text-meta">Loading…</div>
          )}
        </div>
      </Card>
    </>
  );
}
