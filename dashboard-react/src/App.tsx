import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ChevronRight, Mic, Sparkles, Wifi, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CascadeRow } from "@/components/CascadeRow";
import { MicDialog } from "@/components/MicDialog";
import { BrowserPanel, type BrowserPanelState } from "@/components/BrowserPanel";
import { useEventBus } from "@/hooks/useEventBus";
import { useMicRecorder } from "@/hooks/useMicRecorder";
import { audioQueue, fxBuzz, fxChime, fxDoneStep, fxTick, fxZoom } from "@/lib/audio-fx";
import { cn, fmtEur } from "@/lib/utils";
import type { BusEvent, CascadeRow as Row, HealthInfo } from "@/lib/types";

// ---------- cascade reducer ----------
type CascadeState = {
  rows: Row[];
  inFlight: string[]; // FIFO of row ids awaiting step_finished
};

type Action =
  | { type: "reset" }
  | { type: "started"; row: Row }
  | { type: "finished"; result: Record<string, unknown>; tool: string }
  | { type: "error"; tool: string; error: string }
  | { type: "draftResolved"; status: string; draft_id: number };

function describeStarted(p: Record<string, any>): { title: string; body?: string; amount?: string; amountKind?: Row["amountKind"] } {
  const t = p.tool;
  if (t === "pay_vendor")                 return { title: `Pay ${p.vendor || "vendor"}`,                       body: p.description || "",                                                     amount: `−${fmtEur(p.amount_eur)}`, amountKind: "neg" };
  if (t === "create_draft_payment")       return { title: `Draft → ${p.vendor}`,                              body: (p.description || "") + " · awaiting approval",                          amount: fmtEur(p.amount_eur),       amountKind: "pending" };
  if (t === "schedule_recurring_payment") return { title: `Schedule ${p.unit || "WEEKLY"} → ${p.counterparty || "?"}`, amount: fmtEur(p.amount_eur) };
  if (t === "request_money")              return { title: `Request ${fmtEur(p.amount_eur)}`,                  body: p.description || "" };
  if (t === "create_bunqme_link")         return { title: `Share link ${fmtEur(p.amount_eur)}`,                body: p.description || "" };
  if (t === "freeze_home_card")           return { title: `Freeze home card`,                                  body: "Lock against fraud abroad" };
  if (t === "unfreeze_home_card")         return { title: `Unfreeze home card` };
  if (t === "set_card_status")            return { title: `Card → ${p.status}` };
  if (t === "book_restaurant")            return { title: `Book restaurant`,                                  body: `Hint: ${p.restaurant_hint || "?"} · ≤ ${fmtEur(p.max_budget_eur)} · ${p.when || ""}` };
  if (t === "book_hotel")                 return { title: `Book hotel · ${p.city || "?"}`,                    body: `${p.nights || "?"} night(s) · ≤ ${fmtEur(p.max_budget_eur)}` };
  if (t === "subscribe_to_service")       return { title: `Pick a ${p.category || "?"} plan`,                 body: `Browser comparison · ≤ ${fmtEur(p.max_monthly_eur)}/mo` };
  if (t === "send_slack_message")         return { title: `Slack · ${p.header || "Mission Agent"}`,           body: p.preview || "" };
  if (t === "create_calendar_event")      return { title: `Calendar · ${p.title || "Event"}`,                 body: (p.when || "") + (p.invitees && p.invitees.length ? ` · ${p.invitees.join(", ")}` : "") };
  return { title: t };
}

function describeFinishedExtras(tool: string, r: Record<string, any>): string {
  if (tool === "pay_vendor" && r.payment_id)            return `payment_id=${r.payment_id}`;
  if (tool === "create_draft_payment" && r.draft_id)    return `draft_id=${r.draft_id} · pending ${fmtEur(r.amount_eur)}`;
  if (tool === "schedule_recurring_payment" && r.schedule_id) return `schedule_id=${r.schedule_id} · ${r.unit}`;
  if (tool === "request_money" && r.request_id)         return `request_id=${r.request_id}`;
  if (tool === "create_bunqme_link" && r.bunqme_tab_id) return `tab_id=${r.bunqme_tab_id}`;
  if (tool === "create_calendar_event" && r.event_id)   return `event_id=${String(r.event_id).slice(0, 12)}…`;
  if (tool === "send_slack_message" && r.ok)            return "delivered";
  if (tool === "book_restaurant" && r.restaurant_name)  return `${r.restaurant_name} · ${fmtEur(r.price_eur)} · ref ${r.reference || ""}`;
  if (tool === "book_hotel" && r.hotel_name)            return `${r.hotel_name} · ${fmtEur(r.price_eur)} (${r.nights || "?"}n) · ref ${r.reference || ""}`;
  if (tool === "subscribe_to_service" && r.service_name) return `${r.service_name}${r.plan ? " · " + r.plan : ""} · ${fmtEur(r.monthly_eur)}/mo`;
  if (tool === "set_card_status" && r.card_id)          return `card_id=${r.card_id} · ${r.status}`;
  if (tool === "freeze_home_card" && r.card_id)         return `card_id=${r.card_id} · DEACTIVATED`;
  return "";
}

function cascadeReducer(s: CascadeState, a: Action): CascadeState {
  switch (a.type) {
    case "reset":
      return { rows: [], inFlight: [] };
    case "started":
      return { rows: [...s.rows, a.row], inFlight: [...s.inFlight, a.row.id] };
    case "finished": {
      const id = s.inFlight[0];
      if (!id) return s;
      const ids = describeFinishedExtras(a.tool, a.result);
      const rows = s.rows.map((r) =>
        r.id === id
          ? {
              ...r,
              state: a.tool === "create_draft_payment" ? "pending" as const : "complete" as const,
              ids: ids || r.ids,
            }
          : r
      );
      return { rows, inFlight: s.inFlight.slice(1) };
    }
    case "error": {
      const id = s.inFlight[0];
      if (!id) return s;
      const rows = s.rows.map((r) =>
        r.id === id
          ? { ...r, state: "error" as const, ids: "ERROR · " + a.error }
          : r
      );
      return { rows, inFlight: s.inFlight.slice(1) };
    }
    case "draftResolved": {
      const rows = s.rows.map((r) => {
        if (r.tool !== "create_draft_payment") return r;
        if (a.status === "ACCEPTED") {
          const v = parseFloat((r.amount?.match(/[\d.]+/) || ["0"])[0]);
          return {
            ...r,
            state: "complete" as const,
            ids: `draft_id=${a.draft_id} · accepted`,
            amount: !Number.isNaN(v) ? `−${fmtEur(v)}` : r.amount,
            amountKind: "neg" as const,
          };
        }
        return { ...r, state: "error" as const, ids: `draft_id=${a.draft_id} · ${a.status}` };
      });
      return { ...s, rows };
    }
  }
}

// ---------- component ----------
export function App() {
  const [cascade, dispatch] = useReducer(cascadeReducer, { rows: [], inFlight: [] });
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [voiceCard, setVoiceCard] = useState<{ label: string; line: string; route?: string } | null>(null);
  const [browser, setBrowser] = useState<BrowserPanelState>({ visible: false, zoomed: false, line: "", step: "step 0", shotData: null, changing: false });
  const [draft, setDraft] = useState<{ status: "pending" | "accepted" | "rejected" | "timeout" | "running"; title: string; msg: string } | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [balance, setBalance] = useState<{ value: number; step: string } | null>(null);
  const [narration, setNarration] = useState<string | null>(null);
  const [micOpen, setMicOpen] = useState(false);
  const browserUnzoomTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const narrationTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idCounter = useRef(0);
  const newId = () => `r-${++idCounter.current}`;

  // -- mic --
  const mic = useMicRecorder({
    maxDurationSec: 30,
    onTranscribed: (resp) => {
      if (!resp.ok) alert("Live-mic start failed: " + (resp.error || "unknown"));
      setMicOpen(false);
    },
  });

  const onMicClick = useCallback(async () => {
    setMicOpen(true);
    try {
      const r = await fetch("/tts/opening", { method: "POST" });
      const j = await r.json();
      if (j.ok && j.url) audioQueue.enqueue(j.url);
    } catch { /* ignore */ }
    void mic.start();
  }, [mic]);

  const onMicCancel = useCallback(() => {
    mic.cancel();
    setMicOpen(false);
  }, [mic]);

  // -- bus --
  const handleEvent = useCallback((ev: BusEvent) => {
    switch (ev.type) {
      case "mission_started":
        dispatch({ type: "reset" });
        setVoiceCard(null);
        setBrowser({ visible: false, zoomed: false, line: "", step: "step 0", shotData: null, changing: false });
        setDraft(null);
        setSummary(null);
        audioQueue.reset();
        break;

      case "balance_snapshot":
        setBalance({ value: Number(ev.primary_balance_eur), step: String(ev.step) });
        break;

      case "step_started": {
        fxTick();
        const desc = describeStarted(ev as any);
        const id = newId();
        dispatch({
          type: "started",
          row: {
            id,
            tool: String(ev.tool),
            title: desc.title,
            body: desc.body,
            amount: desc.amount,
            amountKind: desc.amountKind,
            state: "running",
          },
        });
        break;
      }

      case "step_finished":
        fxDoneStep();
        dispatch({ type: "finished", tool: String(ev.tool), result: (ev.result as Record<string, unknown>) || {} });
        break;

      case "step_error":
        fxBuzz();
        dispatch({ type: "error", tool: String(ev.tool), error: String(ev.error || "") });
        break;

      case "narrate":
        setNarration(String(ev.text || ""));
        if (narrationTimer.current) clearTimeout(narrationTimer.current);
        narrationTimer.current = setTimeout(() => setNarration(null), 6500);
        break;

      case "narrate_audio":
        if (typeof ev.url === "string") audioQueue.enqueue(ev.url);
        break;

      case "voice_capture_started":
        setVoiceCard({ label: "Listening", line: "Transcribing your mission with ElevenLabs Scribe." });
        break;

      case "transcript_ready":
        setVoiceCard((vc) => ({ label: "Transcript", line: `“${ev.text || ""}”`, route: vc?.route }));
        break;

      case "mission_routed":
        setVoiceCard((vc) => ({ ...(vc || { label: "Transcript", line: "" }), route: `→ ${ev.display}` }));
        break;

      case "browser_started": {
        fxZoom();
        let line = "Browsing live page…";
        if (ev.task === "book_restaurant")        line = `Booking · ${ev.restaurant_hint || "restaurant"} · ≤ ${fmtEur(Number(ev.max_budget))}`;
        else if (ev.task === "book_hotel")         line = `Booking · ${ev.city || "hotel"} · ${ev.nights || "?"} night(s) · ≤ ${fmtEur(Number(ev.max_budget))}`;
        else if (ev.task === "subscribe_to_service") line = `Comparing · ${ev.category || "plans"} · ≤ ${fmtEur(Number(ev.max_monthly_eur))}/mo`;
        setBrowser({ visible: true, zoomed: true, line, step: "step 0", shotData: null, changing: false });
        break;
      }

      case "browser_screenshot":
        setBrowser((b) => ({
          ...b,
          visible: true,
          shotData: typeof ev.b64 === "string" ? ev.b64 : b.shotData,
          step: typeof ev.label === "string" ? ev.label : b.step,
          changing: true,
        }));
        // clear "changing" after a tick to let CSS transition show
        setTimeout(() => setBrowser((b) => ({ ...b, changing: false })), 180);
        break;

      case "browser_action": {
        const a = String(ev.action || "");
        const t = ev.text ? ` → "${ev.text}"` : ev.seconds ? ` ${ev.seconds}s` : "";
        setBrowser((b) => ({ ...b, line: `${a}${t}` }));
        break;
      }

      case "browser_complete": {
        let line = "Done";
        if (ev.restaurant_name) line = `Booked · ${ev.restaurant_name} · ${ev.time_slot || ""} · ${fmtEur(Number(ev.price_eur))}`;
        else if (ev.hotel_name)  line = `Booked · ${ev.hotel_name} · ${ev.nights || "?"}n · ${fmtEur(Number(ev.price_eur))}`;
        else if (ev.service_name) line = `Confirmed · ${ev.service_name}${ev.plan ? " · " + ev.plan : ""} · ${fmtEur(Number(ev.monthly_eur))}/mo`;
        setBrowser((b) => ({ ...b, line, step: "complete" }));
        if (browserUnzoomTimer.current) clearTimeout(browserUnzoomTimer.current);
        browserUnzoomTimer.current = setTimeout(() => setBrowser((b) => ({ ...b, zoomed: false })), 1800);
        break;
      }

      case "awaiting_draft_approval":
        setDraft({
          status: "pending",
          title: "Awaiting your tap",
          msg: `Approve the draft on the bunq sandbox app. Polling for ${Math.floor(Number(ev.timeout_s) || 60)}s.`,
        });
        break;

      case "draft_resolved":
      case "draft_final": {
        const status = String(ev.status || "TIMEOUT").toUpperCase();
        setDraft({
          status: status === "ACCEPTED" ? "accepted" : status === "REJECTED" ? "rejected" : "timeout",
          title: status === "ACCEPTED" ? "Approved" : `Draft ${status}`,
          msg: `Draft ${ev.draft_id} → ${status}.`,
        });
        dispatch({ type: "draftResolved", status, draft_id: Number(ev.draft_id) });
        break;
      }

      case "mission_complete":
        fxChime();
        setSummary(String(ev.summary || "Mission complete"));
        break;

      case "mission_error":
        setSummary("Error · " + String(ev.error || ""));
        break;

      default:
        // bunq_webhook, etc — log only
        break;
    }
  }, []);

  const status = useEventBus(handleEvent);

  // -- health --
  useEffect(() => {
    let cancelled = false;
    fetch("/health")
      .then((r) => r.json())
      .then((j: HealthInfo) => { if (!cancelled) setHealth(j); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const draftBadgeVariant = useMemo(() => {
    if (!draft) return "outline" as const;
    if (draft.status === "accepted") return "complete" as const;
    if (draft.status === "rejected" || draft.status === "timeout") return "overdue" as const;
    return "upcoming" as const;
  }, [draft]);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar */}
      <header className="sticky top-0 z-30 flex items-center gap-3 px-6 h-12 border-b border-border bg-background/95 backdrop-blur">
        <span className="w-5 h-5 rounded-md bg-punctual/15 grid place-items-center">
          <Sparkles className="w-3 h-3 text-punctual" />
        </span>
        <span className="text-foreground font-medium tracking-tight">Mission Mode</span>
        <span className="label-uc text-muted-foreground">bunq · sandbox</span>
        <div className="flex-1" />
        <div className="flex items-center gap-3">
          <span className={cn(
            "flex items-center gap-2 label-uc",
            status === "live" ? "text-status-complete" : "text-muted-foreground"
          )}>
            {status === "live"
              ? <Wifi className="w-3 h-3" />
              : <WifiOff className="w-3 h-3" />}
            {status === "live" ? "Live" : status === "reconnecting" ? "Reconnecting" : "Connecting"}
          </span>
          <Separator />
          <span className="label-uc text-muted-foreground tabular">
            user {health?.user_id ?? "—"}
          </span>
        </div>
      </header>

      {/* Body */}
      <div className="grid grid-cols-[280px_minmax(0,1fr)] flex-1">
        {/* Sidebar */}
        <aside className="border-r border-border bg-background px-5 py-6 flex flex-col gap-7">
          <Section label="Account">
            <div className="text-body text-paper-300">
              {health?.user_id ? `Primary · id ${health.primary_id}` : "Server unreachable"}
            </div>
            <div className="text-meta tabular text-muted-foreground mt-1">
              {health?.public_url ? "real-time webhook" : "polling-only mode"}
            </div>
          </Section>
          <Section label="Balance">
            <div className={cn(
              "text-title tabular transition-colors duration-default",
              balance ? "text-status-complete" : "text-foreground"
            )}>
              {balance ? fmtEur(balance.value) : "€--"}
            </div>
            <div className="text-meta text-muted-foreground mt-1">
              {balance ? `after ${balance.step}` : "awaiting first event"}
            </div>
          </Section>
          <Section label="Voice">
            <div className="text-body text-paper-300">Chris</div>
            <div className="text-meta text-muted-foreground">
              ElevenLabs · Charming, Down-to-Earth
            </div>
          </Section>
          <Section label="Connection" className="mt-auto">
            <div className="text-body text-paper-300 truncate">
              {health?.public_url ? health.public_url.replace(/^https?:\/\//, "") : "polling-only"}
            </div>
          </Section>
        </aside>

        {/* Main */}
        <main className="px-8 py-8 flex flex-col gap-6">
          {/* Hero */}
          <Card className="p-8">
            <div className="flex items-start gap-6">
              <Button
                size="xl"
                onClick={onMicClick}
                disabled={!health?.user_id}
                className="shrink-0 shadow-[0_0_0_1px_rgba(255,255,255,0.06),0_8px_24px_rgba(74,90,122,0.25)]"
              >
                <Mic className="w-7 h-7" />
              </Button>
              <div className="flex-1 min-w-0">
                <h1 className="text-title-lg text-foreground mb-1">What's the mission?</h1>
                <p className="text-body text-paper-400 mb-4">
                  Tap the mic and speak it like you'd say it to a friend. The agent figures out
                  which mission to run and handles the rest.
                </p>
                <div className="flex items-center gap-3 flex-wrap text-meta text-muted-foreground">
                  <span>Try:</span>
                  <span className="kbd">Five hundred for me and Sara, weekend</span>
                  <span className="kbd">Lock in this month's bills</span>
                  <span className="kbd">Tokyo Friday, freeze the card</span>
                </div>
              </div>
            </div>
          </Card>

          {/* Voice card */}
          {voiceCard && (
            <Card className="p-5 animate-in fade-in slide-in-from-top-2 duration-default ease-out">
              <div className="flex items-center gap-3 mb-2">
                <span className="label-uc text-muted-foreground">{voiceCard.label}</span>
                {voiceCard.route && (
                  <span className="ml-auto label-uc text-paper-300">{voiceCard.route}</span>
                )}
              </div>
              <div className="text-body text-foreground">{voiceCard.line}</div>
            </Card>
          )}

          {/* Browser panel */}
          <BrowserPanel state={browser} />

          {/* Cascade */}
          <Card className="overflow-hidden p-0">
            <div className="flex items-center justify-between px-5 py-3 border-b border-border">
              <div className="label-uc text-muted-foreground">Activity</div>
              <div className="label-uc tabular text-muted-foreground">
                {cascade.rows.length} action{cascade.rows.length === 1 ? "" : "s"}
              </div>
            </div>
            {cascade.rows.length === 0 ? (
              <CardContent className="px-5 py-12 text-center">
                <div className="text-body text-muted-foreground">No mission running yet.</div>
                <div className="text-meta text-muted-foreground mt-1">Tap the mic above to start.</div>
              </CardContent>
            ) : (
              <div className="flex flex-col">
                {cascade.rows.map((r, i) => (
                  <CascadeRow key={r.id} row={r} isFirst={i === 0} />
                ))}
              </div>
            )}
          </Card>

          {/* Draft banner */}
          {draft && (
            <Card
              className={cn(
                "flex items-center gap-4 p-4 border-l-2 animate-in fade-in slide-in-from-top-1 duration-default ease-out",
                draft.status === "accepted" && "border-l-status-complete",
                draft.status === "rejected" && "border-l-status-overdue",
                draft.status === "timeout" && "border-l-status-overdue",
                draft.status === "pending" && "border-l-status-upcoming",
              )}
            >
              <Badge variant={draftBadgeVariant} className="shrink-0">
                <span className={cn(
                  "inline-block w-1.5 h-1.5 rounded-full",
                  draft.status === "accepted" && "bg-status-complete",
                  draft.status === "rejected" && "bg-status-overdue",
                  draft.status === "timeout"  && "bg-status-overdue",
                  draft.status === "pending"  && "bg-status-upcoming animate-pulse",
                )} />
                {draft.status.toUpperCase()}
              </Badge>
              <div className="flex-1 min-w-0">
                <div className="text-body text-foreground">{draft.title}</div>
                <div className="text-meta text-muted-foreground">{draft.msg}</div>
              </div>
            </Card>
          )}

          {/* Summary */}
          {summary && (
            <Card className="p-5 animate-in fade-in slide-in-from-bottom-2 duration-default ease-out">
              <div className="label-uc text-muted-foreground mb-2">Mission complete</div>
              <div className="text-body text-foreground">{summary}</div>
            </Card>
          )}
        </main>
      </div>

      {/* Narration footer */}
      <div
        className={cn(
          "fixed bottom-0 left-0 right-0 z-30 transition-transform duration-slow ease-[cubic-bezier(0.2,0,0,1)]",
          narration ? "translate-y-0" : "translate-y-full"
        )}
      >
        <div className="bg-background/95 backdrop-blur border-t border-border">
          <div className="flex items-center gap-3 px-6 py-3 max-w-7xl mx-auto">
            <span className="inline-block w-2 h-2 rounded-full bg-status-complete animate-pulse" />
            <span className="label-uc text-muted-foreground">Voice</span>
            <span className="text-body text-foreground italic flex-1">{narration}</span>
          </div>
        </div>
      </div>

      {/* Mic dialog */}
      <MicDialog
        open={micOpen}
        state={mic.state}
        seconds={mic.seconds}
        analyser={mic.analyser.current}
        onStop={mic.stop}
        onCancel={onMicCancel}
      />
    </div>
  );
}

// --- helpers ---
function Section({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="label-uc text-muted-foreground mb-2">{label}</div>
      {children}
    </div>
  );
}

function Separator() {
  return <span className="label-uc text-muted-foreground select-none">|</span>;
}
