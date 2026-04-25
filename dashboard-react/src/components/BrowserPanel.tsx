import { useEffect, useState } from "react";
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
  const [shotKey, setShotKey] = useState(0);
  useEffect(() => { setShotKey((k) => k + 1); }, [state.shotData]);
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
            "grid place-items-center",
          )}
        >
          {state.shotData ? (
            <motion.img
              key={shotKey}
              initial={{ opacity: 0.65 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              src={`data:image/png;base64,${state.shotData}`}
              className="w-full h-full object-contain"
              alt=""
            />
          ) : (
            <div className="text-muted-foreground text-meta">Loading…</div>
          )}
        </div>
      </Card>
    </motion.div>
  );
}
