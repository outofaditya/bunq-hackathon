import { AnimatePresence, motion } from "framer-motion";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AnimatedNumber } from "@/components/AnimatedNumber";
import { cn, fmtEur } from "@/lib/utils";
import type { CouncilVerdict, Persona, PersonaLine } from "@/lib/types";

// Pull a leading emoji off a label (everything before the first space).
function splitEmoji(name: string): { emoji: string; rest: string } {
  const trimmed = name.trim();
  const idx = trimmed.indexOf(" ");
  if (idx <= 0) return { emoji: "💰", rest: trimmed };
  const head = trimmed.slice(0, idx);
  // Heuristic: if the head contains a non-letter, treat it as emoji.
  if (/[^A-Za-z0-9·.]/.test(head)) {
    return { emoji: head, rest: trimmed.slice(idx + 1).replace(/ ·\s?MM$/, "") };
  }
  return { emoji: "💰", rest: trimmed.replace(/ ·\s?MM$/, "") };
}

const stanceBg: Record<PersonaLine["stance"], string> = {
  for:     "bg-status-complete/10 border-status-complete/35",
  against: "bg-status-overdue/10 border-status-overdue/35",
  neutral: "bg-card border-border/80",
};

const stanceDot: Record<PersonaLine["stance"], string> = {
  for:     "bg-status-complete",
  against: "bg-status-overdue",
  neutral: "bg-paper-400",
};

const verdictTone: Record<CouncilVerdict["verdict"], { text: string; ring: string; chip: string }> = {
  APPROVE:    { text: "text-status-complete", ring: "border-status-complete/40", chip: "bg-status-complete/12 text-status-complete" },
  REJECT:     { text: "text-status-overdue",  ring: "border-status-overdue/40",  chip: "bg-status-overdue/12 text-status-overdue"   },
  COMPROMISE: { text: "text-status-upcoming", ring: "border-status-upcoming/40", chip: "bg-status-upcoming/12 text-status-upcoming" },
};

export type CouncilState = {
  personas: Persona[];
  lines: PersonaLine[];
  verdict: CouncilVerdict | null;
  // payouts: persona_id → cumulative euros sent to that persona this council
  payouts: Record<number, number>;
};

const FADE_UP = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit:    { opacity: 0, y: -6 },
  transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] as const },
};

export function CouncilPanel({ state }: { state: CouncilState }) {
  const { personas, lines, verdict, payouts } = state;
  // Most-recent line per persona, used to pin a bubble next to each tile.
  const lastLineByPersona = new Map<number, PersonaLine>();
  for (const l of lines) lastLineByPersona.set(l.persona_id, l);

  return (
    <Card className="h-full flex flex-col p-0 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70 shrink-0">
        <div className="label-uc text-muted-foreground">The Council</div>
        <div className="label-uc tabular text-muted-foreground">
          {personas.length} voice{personas.length === 1 ? "" : "s"}
        </div>
      </div>

      {/* Persona tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3 overflow-y-auto">
        <AnimatePresence initial={false}>
          {personas.map((p) => {
            const line = lastLineByPersona.get(p.account_id);
            const { emoji, rest } = splitEmoji(p.name);
            const payout = payouts[p.account_id] || 0;
            const stance = line?.stance || "neutral";
            return (
              <motion.div
                key={p.account_id}
                layout
                {...FADE_UP}
                className={cn(
                  "rounded-md border p-3 flex gap-3 transition-colors",
                  stanceBg[stance],
                )}
              >
                <div className="text-2xl leading-none shrink-0 select-none">{emoji}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "inline-block w-1.5 h-1.5 rounded-full shrink-0",
                        stanceDot[stance],
                        line && "animate-pulse",
                      )}
                    />
                    <span className="text-body text-foreground truncate">{rest}</span>
                  </div>
                  <div className="text-meta text-muted-foreground tabular flex items-center gap-2 mt-0.5">
                    <span>{fmtEur(p.balance_eur)}</span>
                    {payout > 0 && (
                      <motion.span
                        key={payout}
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="text-status-complete font-medium"
                      >
                        +<AnimatedNumber value={payout} format={fmtEur} className="" />
                      </motion.span>
                    )}
                  </div>
                  <AnimatePresence mode="wait">
                    {line && (
                      <motion.div
                        key={line.text + lines.length}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
                        className={cn(
                          "text-meta italic mt-1.5 leading-snug",
                          stance === "against" && "text-status-overdue",
                          stance === "for" && "text-status-complete",
                          stance === "neutral" && "text-foreground/85",
                        )}
                      >
                        “{line.text}”
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      {/* Verdict */}
      <AnimatePresence>
        {verdict && (
          <motion.div
            layout
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            className={cn(
              "shrink-0 border-t-2 p-4 flex items-start gap-3",
              verdictTone[verdict.verdict].ring,
            )}
          >
            <Badge className={cn("uppercase shrink-0", verdictTone[verdict.verdict].chip)}>
              {verdict.verdict}
            </Badge>
            <div className="flex-1 min-w-0">
              <div className={cn("text-title leading-snug", verdictTone[verdict.verdict].text)}>
                {verdict.amount_eur > 0 ? fmtEur(verdict.amount_eur) : "—"}{" "}
                <span className="text-foreground">·</span>{" "}
                <span className="text-foreground">{verdict.reasoning}</span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}
