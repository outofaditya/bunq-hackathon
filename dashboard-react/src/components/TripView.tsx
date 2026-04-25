/**
 * TripView — full-page experience for the interactive Trip mission.
 *
 * Two-column layout (chat on left, browser+research on right) that takes over
 * when the user enters trip mode. State is local: chat entries, options, search
 * groups, browser frames, phase. SSE events are routed in via the `event` prop.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Mic, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MarkdownText } from "@/lib/markdown";
import { fmtEur, cn } from "@/lib/utils";
import type {
  BusEvent,
  PackageOption,
  SearchGroup,
  TripChatEntry,
  TripPhase,
} from "@/lib/types";

const SESSION_ID = (() => {
  // Cheap per-tab session id (lives in sessionStorage so refresh starts fresh).
  let id = sessionStorage.getItem("trip-session-id");
  if (!id) {
    id = `trip-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    sessionStorage.setItem("trip-session-id", id);
  }
  return id;
})();

type Props = {
  event: BusEvent | null;
  onExit: () => void;
};

export function TripView({ event, onExit }: Props) {
  const [entries, setEntries] = useState<TripChatEntry[]>([
    { kind: "agent", text: "Hi. I'm your trip agent. **Tell me about the trip you'd like** — destination, dates, who's coming, ballpark budget — and I'll plan it." },
  ]);
  const [phase, setPhase] = useState<TripPhase>("UNDERSTANDING");
  const [searchFeed, setSearchFeed] = useState<SearchGroup[]>([]);
  const [browserFrame, setBrowserFrame] = useState<string | null>(null);
  const [browserStatus, setBrowserStatus] = useState<{ task?: string; query?: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll on new entries
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length]);

  // Route SSE events into local state
  useEffect(() => {
    if (!event) return;
    switch (event.type) {
      case "user_message":
        setEntries((xs) => [...xs, { kind: "user", text: String(event.text || "") }]);
        break;

      case "agent_message": {
        const text = String(event.text || "");
        if (!text.trim()) break;
        setEntries((xs) => [...xs, { kind: "agent", text }]);
        break;
      }

      case "options": {
        const opts = ((event.options as PackageOption[]) || []).map((o) => ({
          ...o,
          image_status: "loading" as const,
        }));
        setEntries((xs) => [...xs, { kind: "options", intro: String(event.intro || ""), options: opts }]);
        break;
      }

      case "option_image": {
        const optionId = String(event.option_id || "");
        const url = (event.image_url as string | null) ?? null;
        const status = (event.status === "ok" ? "ok" : "failed") as "ok" | "failed";
        setEntries((xs) =>
          xs.map((e) => {
            if (e.kind !== "options") return e;
            const idx = e.options.findIndex((o) => o.id === optionId);
            if (idx === -1) return e;
            const next = e.options.slice();
            next[idx] = { ...next[idx], image_url: url, image_status: status };
            return { ...e, options: next };
          }),
        );
        break;
      }

      case "confirmation_request":
        setEntries((xs) => [...xs, { kind: "confirmation", summary: String(event.summary || "") }]);
        break;

      case "trip_phase":
        setPhase(String(event.value || "UNDERSTANDING") as TripPhase);
        break;

      case "search_results": {
        const query = String(event.query || "");
        const results = (event.results as SearchGroup["results"]) || [];
        setSearchFeed((f) => [...f, { query, results }]);
        break;
      }

      case "browser_screenshot": {
        const b64 = typeof event.b64 === "string" ? event.b64 : null;
        if (b64) setBrowserFrame(b64);
        break;
      }

      case "browser_started":
        setBrowserStatus({ task: String(event.task || ""), query: event.query ? String(event.query) : undefined });
        break;

      case "browser_complete":
        // Leave the last frame visible for a moment, then clear.
        setTimeout(() => {
          setBrowserFrame(null);
          setBrowserStatus(null);
        }, 2500);
        break;
    }
  }, [event]);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;
    setSending(true);
    try {
      await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: SESSION_ID, message: text }),
      });
    } catch (err) {
      console.error("chat error", err);
    } finally {
      setSending(false);
    }
  }, []);

  const onSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    void sendMessage(text);
    setDraft("");
  }, [draft, sendMessage]);

  const selectOption = useCallback((idx: number, optionId: string) => {
    setEntries((xs) => xs.map((e, i) => (i === idx && e.kind === "options" ? { ...e, selected: optionId } : e)));
    const entry = entries[idx];
    if (entry?.kind !== "options") return;
    const picked = entry.options.find((o) => o.id === optionId);
    if (!picked) return;
    void sendMessage(`I'll take ${optionId.toUpperCase()} — **${picked.hotel}** with **${picked.restaurant}** for €${picked.total_eur.toFixed(0)}.`);
  }, [entries, sendMessage]);

  const answerConfirmation = useCallback((idx: number, answer: "yes" | "no") => {
    setEntries((xs) => xs.map((e, i) => (i === idx && e.kind === "confirmation" ? { ...e, answered: true } : e)));
    void sendMessage(answer === "yes" ? "Yes, go." : "No, hold off.");
  }, [sendMessage]);

  const placeholder = useMemo(() => {
    if (phase === "EXECUTING") return "Agent is executing…";
    if (phase === "AWAITING_CONFIRMATION") return "Say yes to confirm, or type a change…";
    if (phase === "DONE") return "Mission complete.";
    return "Describe your trip…";
  }, [phase]);

  return (
    <div className="h-full grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] gap-4">
      {/* Left column — chat */}
      <Card className="h-full flex flex-col p-0 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70 shrink-0">
          <div className="flex items-center gap-2">
            <span className="label-uc text-muted-foreground">Trip Agent</span>
            <Badge variant="outline" className="uppercase">{phase.replace("_", " ")}</Badge>
          </div>
          <button onClick={onExit} className="text-muted-foreground hover:text-foreground transition-colors p-1" title="Exit">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-3">
          {entries.map((e, idx) => (
            <ChatEntry
              key={idx}
              entry={e}
              index={idx}
              onSelectOption={selectOption}
              onConfirm={answerConfirmation}
            />
          ))}
        </div>

        <form onSubmit={onSubmit} className="flex items-center gap-2 px-3 py-2 border-t border-border/70 bg-card">
          <input
            type="text"
            value={draft}
            onChange={(ev) => setDraft(ev.target.value)}
            placeholder={placeholder}
            disabled={phase === "DONE"}
            className="flex-1 px-3 py-2 rounded-md bg-paper-900 border border-border/80 text-foreground text-meta focus:outline-none focus:border-punctual"
          />
          <Button type="submit" size="sm" disabled={!draft.trim() || sending || phase === "DONE"}>
            <Send className="w-3.5 h-3.5" />
          </Button>
        </form>
      </Card>

      {/* Right column — browser + research */}
      <div className="h-full flex flex-col gap-3 min-h-0">
        <Card className="p-0 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70">
            <div className="label-uc text-muted-foreground">Agent Browser</div>
            {browserStatus?.query && (
              <div className="text-meta text-punctual font-mono truncate max-w-[60%]">{browserStatus.query}</div>
            )}
          </div>
          <div className="aspect-[900/560] w-full bg-paper-950 grid place-items-center">
            <AnimatePresence mode="wait">
              {browserFrame ? (
                <motion.img
                  key="frame"
                  src={`data:image/jpeg;base64,${browserFrame}`}
                  alt="Browser frame"
                  className="block w-full h-full object-cover object-top"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.18 }}
                />
              ) : (
                <motion.div
                  key="ph"
                  className="text-meta text-muted-foreground text-center px-6"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  Activates when the agent searches the web or books.
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </Card>

        <Card className="flex-1 min-h-0 p-0 overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 border-b border-border/70">
            <div className="label-uc text-muted-foreground">Research · sources found</div>
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto p-3 flex flex-col gap-3">
            {searchFeed.length === 0 && (
              <div className="text-meta text-muted-foreground p-2">No searches yet.</div>
            )}
            {searchFeed.map((g, gi) => (
              <div key={gi} className="flex flex-col gap-1.5">
                <div className="flex items-center gap-2 px-2 py-1.5 bg-paper-800 border border-border/80 rounded-md">
                  <span className="text-meta">🔍</span>
                  <span className="flex-1 text-meta font-mono text-muted-foreground truncate">{g.query}</span>
                  <span className="label-uc text-muted-foreground tabular">{g.results.length}</span>
                </div>
                <ul className="flex flex-col gap-1 list-none m-0 p-0">
                  {g.results.slice(0, 5).map((r, ri) => (
                    <li key={ri}>
                      <a
                        href={r.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="block px-3 py-2 rounded-md border border-border/70 hover:border-punctual transition-colors no-underline"
                      >
                        <div className="text-meta font-medium text-foreground line-clamp-2 leading-snug">{r.title}</div>
                        <div className="label-uc text-punctual mt-0.5 truncate">{hostnameOf(r.url)}</div>
                        {r.snippet && (
                          <div className="text-meta text-muted-foreground mt-1 line-clamp-2 leading-snug">{r.snippet}</div>
                        )}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

// =================== sub-components ===================

function ChatEntry({
  entry,
  index,
  onSelectOption,
  onConfirm,
}: {
  entry: TripChatEntry;
  index: number;
  onSelectOption: (i: number, id: string) => void;
  onConfirm: (i: number, ans: "yes" | "no") => void;
}) {
  if (entry.kind === "user") {
    return (
      <div className="self-end max-w-[80%] px-3 py-2 rounded-2xl rounded-br-sm bg-punctual text-primary-foreground">
        <MarkdownText text={entry.text} />
      </div>
    );
  }
  if (entry.kind === "agent") {
    return (
      <div className="self-start max-w-[85%] px-3 py-2 rounded-2xl rounded-bl-sm bg-paper-800 border border-border/70 text-foreground">
        <MarkdownText text={entry.text} />
      </div>
    );
  }
  if (entry.kind === "options") {
    return (
      <div className="flex flex-col gap-2">
        {entry.intro && (
          <div className="text-meta text-muted-foreground px-1">
            <MarkdownText text={entry.intro} />
          </div>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {entry.options.map((o) => (
            <OptionCard
              key={o.id}
              option={o}
              selected={entry.selected === o.id}
              disabled={!!entry.selected}
              onSelect={() => onSelectOption(index, o.id)}
            />
          ))}
        </div>
      </div>
    );
  }
  if (entry.kind === "confirmation") {
    return (
      <Card className={cn("p-3 border-l-2 border-l-status-upcoming", entry.answered && "opacity-60")}>
        <div className="text-body text-foreground mb-2">{entry.summary}</div>
        {!entry.answered && (
          <div className="flex gap-2">
            <Button size="sm" onClick={() => onConfirm(index, "yes")}>Yes, go</Button>
            <Button size="sm" variant="outline" onClick={() => onConfirm(index, "no")}>Cancel</Button>
          </div>
        )}
      </Card>
    );
  }
  return null;
}

function OptionCard({
  option,
  selected,
  disabled,
  onSelect,
}: {
  option: PackageOption;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  const hasSources = (option.sources?.length ?? 0) > 0;
  return (
    <Card className={cn(
      "overflow-hidden p-0 transition-all",
      selected && "border-punctual ring-1 ring-punctual",
      disabled && !selected && "opacity-50",
    )}>
      <button
        type="button"
        disabled={disabled && !selected}
        onClick={onSelect}
        className="w-full text-left flex flex-col cursor-pointer hover:translate-y-[-1px] transition-transform"
      >
        <div className="aspect-[4/3] bg-paper-900 relative overflow-hidden">
          {option.image_url ? (
            <motion.img
              src={option.image_url}
              alt={option.hotel}
              className="block w-full h-full object-cover"
              initial={{ opacity: 0, scale: 1.04 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.32 }}
            />
          ) : option.image_status === "failed" ? (
            <div className="absolute inset-0 grid place-items-center text-3xl opacity-50">🌍</div>
          ) : (
            <SkeletonShimmer />
          )}
        </div>
        <div className="p-3 flex flex-col gap-1">
          <div className="label-uc text-muted-foreground">{option.id.toUpperCase()}</div>
          <div className="text-body font-semibold text-foreground leading-tight">{option.hotel}</div>
          <div className="text-meta text-muted-foreground">🍽 {option.restaurant}</div>
          <div className="text-meta text-muted-foreground">✨ {option.extra}</div>
          {option.notes && <div className="text-meta italic text-muted-foreground mt-1">{option.notes}</div>}
          <div className="text-body font-bold text-punctual mt-1">{fmtEur(option.total_eur)}</div>
        </div>
      </button>
      {hasSources && (
        <div className="border-t border-border/70 px-3 py-2 flex flex-col gap-1.5">
          <div className="label-uc text-muted-foreground">Sources</div>
          <div className="flex flex-wrap gap-1.5">
            {option.sources!.slice(0, 5).map((s, i) => (
              <a
                key={i}
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-paper-800 border border-border/70 text-meta font-mono text-muted-foreground hover:text-punctual hover:border-punctual transition-colors no-underline truncate max-w-full"
                title={s.url}
              >
                <span className="text-[9px] opacity-70">↗</span>
                {s.label || hostnameOf(s.url)}
              </a>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function SkeletonShimmer() {
  return (
    <div className="absolute inset-0 grid place-items-center">
      <div className="absolute inset-0 bg-gradient-to-br from-paper-800 to-paper-900 animate-pulse" />
      <div className="relative z-10 label-uc text-muted-foreground bg-paper-950/70 px-2 py-1 rounded-full border border-border/70 backdrop-blur">
        painting…
      </div>
    </div>
  );
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// Avoid unused-import lint
void Mic;
