import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Mic, Sparkles, Wifi, WifiOff } from "lucide-react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CascadeRow } from "@/components/CascadeRow";
import { MicDialog } from "@/components/MicDialog";
import { BrowserPanel, type BrowserPanelState } from "@/components/BrowserPanel";
import { CouncilPanel, type CouncilState } from "@/components/CouncilPanel";
import { TripView } from "@/components/TripView";
import { AnimatedNumber } from "@/components/AnimatedNumber";
import { useEventBus } from "@/hooks/useEventBus";
import { useMicRecorder } from "@/hooks/useMicRecorder";
import { audioQueue, fxBuzz, fxChime, fxDoneStep, fxTick, fxZoom } from "@/lib/audio-fx";
import { cn, fmtEur } from "@/lib/utils";
import type {
  BusEvent, CascadeRow as Row, CouncilVerdict, HealthInfo, Persona, PersonaLine,
} from "@/lib/types";

// =================== cascade reducer ===================
type CascadeState = { rows: Row[]; inFlight: string[] };
type Action =
  | { type: "reset" }
  | { type: "started"; row: Row }
  | { type: "finished"; result: Record<string, unknown>; tool: string }
  | { type: "error"; tool: string; error: string }
  | { type: "draftResolved"; status: string; draft_id: number };

function describeStarted(p: Record<string, any>): { title: string; body?: string; amount?: string; amountKind?: Row["amountKind"] } {
  const t = p.tool;
  if (t === "pay_vendor")                 return { title: `Pay ${p.vendor || "vendor"}`,                                  body: p.description || "", amount: `−${fmtEur(p.amount_eur)}`, amountKind: "neg" };
  if (t === "create_draft_payment")       return { title: `Draft → ${p.vendor}`,                                          body: (p.description || "") + " · awaiting your phone tap", amount: fmtEur(p.amount_eur), amountKind: "pending" };
  if (t === "schedule_recurring_payment") return { title: `Schedule ${p.unit || "WEEKLY"} → ${p.counterparty || "?"}`,    amount: fmtEur(p.amount_eur) };
  if (t === "request_money")              return { title: `Request ${fmtEur(p.amount_eur)}`,                              body: p.description || "" };
  if (t === "create_bunqme_link")         return { title: `Share link ${fmtEur(p.amount_eur)}`,                            body: p.description || "" };
  if (t === "freeze_home_card")           return { title: `Freeze home card`,                                              body: "Lock against fraud abroad" };
  if (t === "unfreeze_home_card")         return { title: `Unfreeze home card` };
  if (t === "set_card_status")            return { title: `Card → ${p.status}` };
  if (t === "book_restaurant")            return { title: `Book restaurant`,                                              body: `Hint: ${p.restaurant_hint || "?"} · ≤ ${fmtEur(p.max_budget_eur)} · ${p.when || ""}` };
  if (t === "book_hotel")                 return { title: `Book hotel · ${p.city || "?"}`,                                body: `${p.nights || "?"} night(s) · ≤ ${fmtEur(p.max_budget_eur)}` };
  if (t === "subscribe_to_service")       return { title: `Pick a ${p.category || "?"} plan`,                              body: `Browser comparison · ≤ ${fmtEur(p.max_monthly_eur)}/mo` };
  if (t === "send_slack_message")         return { title: `Slack · ${p.header || "Mission Agent"}`,                       body: p.preview || "" };
  if (t === "create_calendar_event")      return { title: `Calendar · ${p.title || "Event"}`,                              body: (p.when || "") + (p.invitees?.length ? ` · ${p.invitees.join(", ")}` : "") };
  return { title: t };
}
function describeFinishedExtras(tool: string, r: Record<string, any>): string {
  if (tool === "pay_vendor" && r.payment_id)                  return `payment_id=${r.payment_id}`;
  if (tool === "create_draft_payment" && r.draft_id)          return `draft_id=${r.draft_id} · pending ${fmtEur(r.amount_eur)}`;
  if (tool === "schedule_recurring_payment" && r.schedule_id) return `schedule_id=${r.schedule_id} · ${r.unit}`;
  if (tool === "request_money" && r.request_id)               return `request_id=${r.request_id}`;
  if (tool === "create_bunqme_link" && r.bunqme_tab_id)       return `tab_id=${r.bunqme_tab_id}`;
  if (tool === "create_calendar_event" && r.event_id)         return `event_id=${String(r.event_id).slice(0, 12)}…`;
  if (tool === "send_slack_message" && r.ok)                  return "delivered";
  if (tool === "book_restaurant" && r.restaurant_name)        return `${r.restaurant_name} · ${fmtEur(r.price_eur)} · ref ${r.reference || ""}`;
  if (tool === "book_hotel" && r.hotel_name)                  return `${r.hotel_name} · ${fmtEur(r.price_eur)} (${r.nights || "?"}n) · ref ${r.reference || ""}`;
  if (tool === "subscribe_to_service" && r.service_name)      return `${r.service_name}${r.plan ? " · " + r.plan : ""} · ${fmtEur(r.monthly_eur)}/mo`;
  if (tool === "set_card_status" && r.card_id)                return `card_id=${r.card_id} · ${r.status}`;
  if (tool === "freeze_home_card" && r.card_id)               return `card_id=${r.card_id} · DEACTIVATED`;
  return "";
}
function cascadeReducer(s: CascadeState, a: Action): CascadeState {
  switch (a.type) {
    case "reset":   return { rows: [], inFlight: [] };
    case "started": return { rows: [...s.rows, a.row], inFlight: [...s.inFlight, a.row.id] };
    case "finished": {
      const id = s.inFlight[0]; if (!id) return s;
      const ids = describeFinishedExtras(a.tool, a.result);
      return {
        rows: s.rows.map((r) => r.id === id ? { ...r, state: a.tool === "create_draft_payment" ? "pending" as const : "complete" as const, ids: ids || r.ids } : r),
        inFlight: s.inFlight.slice(1),
      };
    }
    case "error": {
      const id = s.inFlight[0]; if (!id) return s;
      return {
        rows: s.rows.map((r) => r.id === id ? { ...r, state: "error" as const, ids: "ERROR · " + a.error } : r),
        inFlight: s.inFlight.slice(1),
      };
    }
    case "draftResolved": {
      const rows = s.rows.map((r) => {
        if (r.tool !== "create_draft_payment") return r;
        if (a.status === "ACCEPTED") {
          const v = parseFloat((r.amount?.match(/[\d.]+/) || ["0"])[0]);
          return { ...r, state: "complete" as const, ids: `draft_id=${a.draft_id} · accepted`, amount: !Number.isNaN(v) ? `−${fmtEur(v)}` : r.amount, amountKind: "neg" as const };
        }
        return { ...r, state: "error" as const, ids: `draft_id=${a.draft_id} · ${a.status}` };
      });
      return { ...s, rows };
    }
  }
}

// =================== animation presets ===================
const PUNCTUAL_EASE = [0.16, 1, 0.3, 1] as const;
const FADE_UP = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit:    { opacity: 0, y: -6 },
  transition: { duration: 0.32, ease: PUNCTUAL_EASE },
};

// =================== component ===================
type ViewState = "idle" | "voice" | "running" | "complete" | "trip";

export function App() {
  const [cascade, dispatch] = useReducer(cascadeReducer, { rows: [], inFlight: [] });
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [voiceCard, setVoiceCard] = useState<{ label: string; line: string; route?: string } | null>(null);
  const [browser, setBrowser] = useState<BrowserPanelState>({ visible: false, line: "", step: "step 0", shotData: null, changing: false });
  const [council, setCouncil] = useState<CouncilState>({ personas: [], lines: [], verdict: null, payouts: {} });
  const [draft, setDraft] = useState<{ status: "pending" | "accepted" | "rejected" | "timeout"; title: string; msg: string } | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [narration, setNarration] = useState<string | null>(null);
  const [micOpen, setMicOpen] = useState(false);
  const [view, setView] = useState<ViewState>("idle");
  // Genesis flow — runs automatically before any user voice interaction.
  const [genesisStarted, setGenesisStarted] = useState(false);
  const [genesisDone, setGenesisDone] = useState(false);
  const [genesisStep, setGenesisStep] = useState<{ label: string; emoji: string } | null>(null);
  // Confirmation flow at end of council — winner persona asks user out loud.
  const [awaitingConfirm, setAwaitingConfirm] = useState<{ question: string; action_summary: string; winning_persona_id: number | null } | null>(null);
  const [userConfirm, setUserConfirm] = useState<{ transcript: string; decision: string; picked_name?: string | null } | null>(null);
  const [voteOpen, setVoteOpen] = useState(false);
  // Trip mission — interactive chat takeover.
  const [tripEvent, setTripEvent] = useState<BusEvent | null>(null);
  const narrationTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const idCounter = useRef(0);
  const newId = () => `r-${++idCounter.current}`;

  const mic = useMicRecorder({
    maxDurationSec: 30,
    onTranscribed: (resp) => {
      if (!resp.ok) alert("Live-mic start failed: " + (resp.error || "unknown"));
      setMicOpen(false);
    },
  });

  // Second recorder dedicated to council confirmation — uploads to a different
  // endpoint, no seed_eur / wait_seconds payload.
  const confirmMic = useMicRecorder({
    maxDurationSec: 12,
    uploadFn: async (blob, mime) => {
      const ext = mime.includes("webm") ? "webm" : mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "bin";
      const fd = new FormData();
      fd.append("audio", blob, `confirm.${ext}`);
      const r = await fetch("/missions/council/confirm", { method: "POST", body: fd });
      return r.json();
    },
    onTranscribed: (resp) => {
      setVoteOpen(false);
      if (!resp.ok) alert("Confirmation upload failed: " + (resp.error || "unknown"));
    },
  });
  const onConfirmCancel = useCallback(() => { confirmMic.cancel(); setVoteOpen(false); }, [confirmMic]);

  const onMicClick = useCallback(async () => {
    setMicOpen(true);
    try {
      const r = await fetch("/tts/opening", { method: "POST" });
      const j = await r.json();
      if (j.ok && j.url) audioQueue.enqueue(j.url);
    } catch { /* ignore */ }
    void mic.start();
  }, [mic]);

  const onMicCancel = useCallback(() => { mic.cancel(); setMicOpen(false); }, [mic]);

  const resetAll = useCallback(() => {
    dispatch({ type: "reset" });
    setVoiceCard(null);
    setBrowser({ visible: false, line: "", step: "step 0", shotData: null, changing: false });
    // Keep council personas across resets — they persist between missions.
    setCouncil((c) => ({ ...c, lines: [], verdict: null, payouts: {} }));
    setDraft(null);
    setSummary(null);
    setAwaitingConfirm(null);
    setUserConfirm(null);
    audioQueue.reset();
    setView("idle");
  }, []);

  const handleEvent = useCallback((ev: BusEvent) => {
    // Forward every event to the TripView (it filters internally).
    setTripEvent(ev);
    switch (ev.type) {
      case "mission_started":
        dispatch({ type: "reset" });
        setVoiceCard(null);
        setBrowser({ visible: false, line: "", step: "step 0", shotData: null, changing: false });
        // Keep personas — they're long-lived. Reset only ephemeral mission state.
        setCouncil((c) => ({ ...c, lines: [], verdict: null, payouts: {} }));
        setAwaitingConfirm(null);
        setUserConfirm(null);
        setDraft(null);
        setSummary(null);
        audioQueue.reset();
        setView("running");
        break;
      case "balance_snapshot":
        setBalance(Number(ev.primary_balance_eur));
        break;
      case "step_started": {
        fxTick();
        const desc = describeStarted(ev as any);
        const id = newId();
        dispatch({ type: "started", row: { id, tool: String(ev.tool), title: desc.title, body: desc.body, amount: desc.amount, amountKind: desc.amountKind, state: "running" } });
        setView("running");
        break;
      }
      case "step_finished": fxDoneStep(); dispatch({ type: "finished", tool: String(ev.tool), result: (ev.result as Record<string, unknown>) || {} }); break;
      case "step_error":    fxBuzz();     dispatch({ type: "error", tool: String(ev.tool), error: String(ev.error || "") }); break;
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
        setView("voice");
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
        if (ev.task === "book_restaurant")           line = `Booking · ${ev.restaurant_hint || "restaurant"} · ≤ ${fmtEur(Number(ev.max_budget))}`;
        else if (ev.task === "book_hotel")            line = `Booking · ${ev.city || "hotel"} · ${ev.nights || "?"} night(s) · ≤ ${fmtEur(Number(ev.max_budget))}`;
        else if (ev.task === "subscribe_to_service")  line = `Comparing · ${ev.category || "plans"} · ≤ ${fmtEur(Number(ev.max_monthly_eur))}/mo`;
        setBrowser({ visible: true, line, step: "step 0", shotData: null, changing: false });
        break;
      }
      case "browser_screenshot":
        setBrowser((b) => ({ ...b, visible: true, shotData: typeof ev.b64 === "string" ? ev.b64 : b.shotData, step: typeof ev.label === "string" ? ev.label : b.step, changing: true }));
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
        if (ev.restaurant_name)   line = `Booked · ${ev.restaurant_name} · ${ev.time_slot || ""} · ${fmtEur(Number(ev.price_eur))}`;
        else if (ev.hotel_name)    line = `Booked · ${ev.hotel_name} · ${ev.nights || "?"}n · ${fmtEur(Number(ev.price_eur))}`;
        else if (ev.service_name)  line = `Confirmed · ${ev.service_name}${ev.plan ? " · " + ev.plan : ""} · ${fmtEur(Number(ev.monthly_eur))}/mo`;
        setBrowser((b) => ({ ...b, line, step: "complete" }));
        // After it completes, leave the panel up but stop adding new shots; never zoom.
        break;
      }
      case "awaiting_draft_approval":
        setDraft({ status: "pending", title: "Awaiting your tap", msg: `Approve the draft on the bunq sandbox app. Polling for ${Math.floor(Number(ev.timeout_s) || 60)}s.` });
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
      case "personas_loaded": {
        const personas = (Array.isArray(ev.personas) ? ev.personas : []) as Persona[];
        setCouncil((c) => ({ ...c, personas }));
        break;
      }
      case "persona_speaks": {
        fxTick();
        const line: PersonaLine = {
          persona_id: Number(ev.persona_id),
          name:       String(ev.name || ""),
          archetype:  String(ev.archetype || ""),
          voice_id:   String(ev.voice_id || ""),
          stance:     (ev.stance === "for" || ev.stance === "against" ? ev.stance : "neutral") as PersonaLine["stance"],
          text:       String(ev.text || ""),
          audio_url:  typeof ev.audio_url === "string" ? ev.audio_url : null,
        };
        setCouncil((c) => ({ ...c, lines: [...c.lines, line] }));
        if (line.audio_url) audioQueue.enqueue(line.audio_url);
        // Surface the line in the narration footer too.
        setNarration(line.text);
        if (narrationTimer.current) clearTimeout(narrationTimer.current);
        narrationTimer.current = setTimeout(() => setNarration(null), 6500);
        break;
      }
      case "persona_payout": {
        fxDoneStep();
        const pid = Number(ev.persona_id);
        const amt = Number(ev.amount_eur || 0);
        setCouncil((c) => {
          const next = { ...(c.payouts || {}) };
          next[pid] = (next[pid] || 0) + amt;
          // Optimistically bump the persona's balance card for instant feedback.
          const personas = c.personas.map((p) =>
            p.account_id === pid ? { ...p, balance_eur: p.balance_eur + amt } : p,
          );
          return { ...c, payouts: next, personas };
        });
        break;
      }
      case "council_verdict": {
        const v: CouncilVerdict = {
          verdict:    (String(ev.verdict || "APPROVE").toUpperCase() as CouncilVerdict["verdict"]),
          amount_eur: Number(ev.amount_eur || 0),
          reasoning:  String(ev.reasoning || ""),
        };
        setCouncil((c) => ({ ...c, verdict: v }));
        fxChime();
        break;
      }
      case "genesis_started": {
        setGenesisStarted(true);
        setGenesisDone(false);
        setCouncil((c) => ({ ...c, personas: [], lines: [], verdict: null, payouts: {} }));
        break;
      }
      case "genesis_step_started": {
        fxTick();
        setGenesisStep({
          label: String(ev.label || "").replace(/ ·\s?MM$/, ""),
          emoji: String(ev.emoji || "💰"),
        });
        break;
      }
      case "genesis_step_finished": {
        fxDoneStep();
        // The matching personas_loaded event already updated the tile list.
        break;
      }
      case "genesis_complete": {
        setGenesisDone(true);
        setGenesisStarted(false);
        setGenesisStep(null);
        break;
      }
      case "genesis_warning":
      case "genesis_error": {
        console.warn("genesis", ev);
        // Unblock the mic anyway so the user isn't stuck.
        setGenesisDone(true);
        setGenesisStarted(false);
        break;
      }
      case "awaiting_confirmation": {
        const ac = {
          question:           String(ev.question || "Should I execute this?"),
          action_summary:     String(ev.action_summary || ""),
          winning_persona_id: ev.winning_persona_id != null ? Number(ev.winning_persona_id) : null,
        };
        setAwaitingConfirm(ac);
        // Auto-open the confirmation mic so the user just speaks.
        setVoteOpen(true);
        void confirmMic.start();
        break;
      }
      case "user_confirmation_received": {
        const pickedName = typeof ev.picked_name === "string" ? ev.picked_name : null;
        setUserConfirm({
          transcript:  String(ev.transcript || ""),
          decision:    String(ev.decision || "unsure"),
          picked_name: pickedName,
        });
        setAwaitingConfirm(null);
        setVoteOpen(false);
        // Highlight the picked persona's tile briefly by injecting a synthetic
        // line so the user sees confirmation that their override registered.
        if (pickedName && ev.picked_persona_id != null) {
          const pid = Number(ev.picked_persona_id);
          setCouncil((c) => ({
            ...c,
            lines: [...c.lines, {
              persona_id: pid,
              name:       pickedName,
              archetype:  "",
              voice_id:   "",
              stance:     "for" as const,
              text:       "User sided with me.",
              audio_url:  null,
            }],
          }));
        }
        break;
      }
      case "user_confirmation_timeout": {
        setAwaitingConfirm(null);
        setUserConfirm({ transcript: "(no answer)", decision: "timeout" });
        setVoteOpen(false);
        break;
      }
      case "mission_complete":
        fxChime();
        setSummary(String(ev.summary || "Mission complete"));
        setView("complete");
        break;
      case "mission_error":
        setSummary("Error · " + String(ev.error || ""));
        setView("complete");
        break;
    }
  }, []);

  const status = useEventBus(handleEvent);

  useEffect(() => {
    let cancelled = false;
    fetch("/health").then((r) => r.json()).then((j: HealthInfo) => { if (!cancelled) setHealth(j); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Auto-genesis: on first load, check the persona registry. If it's empty,
  // kick off the visible Council bring-up. The mic stays disabled until the
  // genesis_complete event arrives.
  useEffect(() => {
    let cancelled = false;
    if (!health?.user_id) return; // wait until bunq is authenticated
    (async () => {
      try {
        const r = await fetch("/personas");
        const j = await r.json();
        if (cancelled) return;
        const personas: Persona[] = Array.isArray(j.personas) ? j.personas : [];
        if (personas.length === 0) {
          setGenesisStarted(true);
          await fetch("/genesis/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
          // events drive the rest of the flow
        } else {
          // Council already populated from a prior run — just load it and unlock mic.
          setCouncil((c) => ({ ...c, personas }));
          setGenesisDone(true);
        }
      } catch (e) {
        console.warn("auto-genesis bootstrap failed:", e);
      }
    })();
    return () => { cancelled = true; };
  }, [health?.user_id]);

  const draftBadgeVariant = useMemo(() => {
    if (!draft) return "outline" as const;
    if (draft.status === "accepted") return "complete" as const;
    if (draft.status === "rejected" || draft.status === "timeout") return "overdue" as const;
    return "upcoming" as const;
  }, [draft]);

  // The Council panel is visible whenever there's anything in the room — during
  // genesis bring-up, idle (after genesis), and any active mission.
  const councilVisible = council.personas.length > 0 || genesisStarted;
  const splitView = councilVisible || view === "running" || (view === "voice" && (browser.visible || cascade.rows.length > 0));
  const micReady = genesisDone && !!health?.user_id;

  return (
    <MotionConfig transition={{ duration: 0.32, ease: PUNCTUAL_EASE }}>
      <div className="h-dvh grid grid-rows-[56px_1fr_auto] bg-background text-foreground overflow-hidden">

        {/* Header */}
        <header className="flex items-center gap-3 px-6 border-b border-border/70 bg-background/80 backdrop-blur">
          <span className="w-6 h-6 rounded-md bg-punctual/15 grid place-items-center">
            <Sparkles className="w-3.5 h-3.5 text-punctual" />
          </span>
          <span className="text-foreground font-medium tracking-tight">Mission Mode</span>
          <div className="flex-1" />

          <AnimatePresence>
            {balance !== null && (
              <motion.div
                {...FADE_UP}
                className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-card border border-border/80"
              >
                <span className="label-uc text-muted-foreground">Balance</span>
                <AnimatedNumber value={balance} format={fmtEur} className="text-meta tabular text-foreground font-medium" />
              </motion.div>
            )}
          </AnimatePresence>

          <div className={cn(
            "flex items-center gap-2 px-3 py-1.5 rounded-full",
            status === "live" ? "text-status-complete" : "text-muted-foreground",
          )}>
            {status === "live" ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />}
            <span className="label-uc">{status === "live" ? "Live" : status === "reconnecting" ? "Reconnecting" : "Connecting"}</span>
          </div>
        </header>

        {/* Body — fills remaining height, never scrolls */}
        <main className="min-h-0 overflow-hidden p-4">
          <div className={cn(
            "h-full grid gap-4 transition-[grid-template-columns] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]",
            splitView ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" : "grid-cols-1",
          )}>

            {/* Left column: cascade + voice + draft */}
            <AnimatePresence mode="wait">
              {splitView && (
                <motion.div
                  key="left"
                  layout
                  {...FADE_UP}
                  className="min-h-0 flex flex-col gap-3 overflow-hidden"
                >
                  <AnimatePresence>
                    {voiceCard && (
                      <motion.div key="vc" layout {...FADE_UP}>
                        <Card className="p-4">
                          <div className="flex items-center gap-3 mb-1.5">
                            <span className="label-uc text-muted-foreground">{voiceCard.label}</span>
                            {voiceCard.route && (
                              <motion.span {...FADE_UP} className="ml-auto label-uc text-paper-300">{voiceCard.route}</motion.span>
                            )}
                          </div>
                          <div className="text-body text-foreground leading-snug">{voiceCard.line}</div>
                        </Card>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Idle-with-council: mic ready, no cascade yet → show the
                      hero so the user has something to tap. */}
                  {view === "idle" && councilVisible && cascade.rows.length === 0 && (
                    <motion.div key="idle-hero" layout {...FADE_UP} className="flex-1 min-h-0 flex">
                      <div className="m-auto">
                        <IdleMic
                          onStart={onMicClick}
                          disabled={!micReady}
                          muted={!micReady}
                          hint={genesisStarted ? `Building the council… ${genesisStep ? `${genesisStep.emoji} ${genesisStep.label}` : ""}` : undefined}
                        />
                      </div>
                    </motion.div>
                  )}

                  {/* Awaiting confirmation banner — shown when the winning
                      persona is asking the user out loud. */}
                  <AnimatePresence>
                    {awaitingConfirm && (
                      <motion.div key="confirm" layout {...FADE_UP}>
                        <Card className="p-4 border-l-2 border-l-status-scheduled bg-status-scheduled/5">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="relative w-2 h-2">
                              <span className="absolute inset-0 rounded-full bg-status-scheduled animate-ping opacity-75" />
                              <span className="absolute inset-0 rounded-full bg-status-scheduled" />
                            </span>
                            <span className="label-uc text-status-scheduled">Listening · your call</span>
                          </div>
                          <div className="text-title text-foreground leading-snug italic">"{awaitingConfirm.question}"</div>
                          {awaitingConfirm.action_summary && (
                            <div className="text-meta tabular text-muted-foreground mt-1.5">
                              On yes: {awaitingConfirm.action_summary}
                            </div>
                          )}
                        </Card>
                      </motion.div>
                    )}
                    {userConfirm && !awaitingConfirm && (
                      <motion.div key="confirm-echo" layout {...FADE_UP}>
                        <Card className="p-3 flex items-center gap-3">
                          <Badge className="uppercase shrink-0">{userConfirm.decision}</Badge>
                          <div className="flex-1 min-w-0">
                            <div className="text-body text-foreground italic truncate">"{userConfirm.transcript}"</div>
                            {userConfirm.picked_name && (
                              <div className="text-meta text-status-scheduled mt-0.5">
                                → sided with {userConfirm.picked_name.replace(/ ·\s?MM$/, "")}
                              </div>
                            )}
                          </div>
                        </Card>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {cascade.rows.length > 0 && (
                    <motion.div layout className="flex-1 min-h-0">
                      <Card className="h-full overflow-hidden p-0 flex flex-col">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/70 shrink-0">
                          <div className="label-uc text-muted-foreground">Activity</div>
                          <div className="label-uc tabular text-muted-foreground">
                            {cascade.rows.length} action{cascade.rows.length === 1 ? "" : "s"}
                          </div>
                        </div>
                        <div className="flex-1 min-h-0 overflow-y-auto">
                          <AnimatePresence initial={false}>
                            {cascade.rows.map((r, i) => (
                              <motion.div key={r.id} layout {...FADE_UP}>
                                <CascadeRow row={r} isFirst={i === 0} index={i} />
                              </motion.div>
                            ))}
                          </AnimatePresence>
                        </div>
                      </Card>
                    </motion.div>
                  )}

                  <AnimatePresence>
                    {draft && (
                      <motion.div key="draft" layout {...FADE_UP}>
                        <Card
                          className={cn(
                            "flex items-center gap-3 p-3 border-l-2",
                            draft.status === "accepted" && "border-l-status-complete",
                            draft.status === "rejected" && "border-l-status-overdue",
                            draft.status === "timeout"  && "border-l-status-overdue",
                            draft.status === "pending"  && "border-l-status-upcoming",
                          )}
                        >
                          <Badge variant={draftBadgeVariant} className="shrink-0 uppercase">
                            <span className={cn(
                              "inline-block w-1.5 h-1.5 rounded-full",
                              draft.status === "accepted" && "bg-status-complete",
                              draft.status === "rejected" && "bg-status-overdue",
                              draft.status === "timeout"  && "bg-status-overdue",
                              draft.status === "pending"  && "bg-status-upcoming animate-pulse",
                            )} />
                            {draft.status}
                          </Badge>
                          <div className="flex-1 min-w-0">
                            <div className="text-body text-foreground leading-snug">{draft.title}</div>
                            <div className="text-meta text-muted-foreground">{draft.msg}</div>
                          </div>
                        </Card>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Right column / centered hero */}
            <motion.div
              layout
              transition={{ duration: 0.45, ease: PUNCTUAL_EASE }}
              className="min-h-0 flex"
            >
              <AnimatePresence mode="wait">
                {/* IDLE — show council if it exists; otherwise centered mic */}
                {view === "idle" && councilVisible && (
                  <motion.div
                    key="idle-council"
                    layout
                    {...FADE_UP}
                    className="w-full h-full"
                  >
                    <CouncilPanel state={council} />
                  </motion.div>
                )}
                {view === "idle" && !councilVisible && (
                  <motion.div
                    key="hero"
                    layout
                    {...FADE_UP}
                    className="m-auto flex flex-col items-center"
                  >
                    <IdleMic
                      onStart={onMicClick}
                      onTripStart={() => {
                        // Reset bus + session, then enter chat-mode.
                        fetch("/chat/reset", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ session_id: sessionStorage.getItem("trip-session-id") || "default" }),
                        }).catch(() => { /* ignore */ });
                        sessionStorage.removeItem("trip-session-id");
                        setView("trip");
                      }}
                      disabled={!micReady}
                      muted={!micReady}
                      hint={genesisStarted ? "Building the council…" : undefined}
                    />
                  </motion.div>
                )}

                {/* TRIP — interactive chat takeover */}
                {view === "trip" && (
                  <motion.div
                    key="trip"
                    layout
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.32 }}
                    className="w-full h-full"
                  >
                    <TripView event={tripEvent} onExit={resetAll} />
                  </motion.div>
                )}

                {/* RUNNING / VOICE — council > browser > agent status */}
                {(view === "running" || view === "voice") && (
                  <motion.div
                    key="right"
                    layout
                    {...FADE_UP}
                    className="w-full h-full"
                  >
                    {council.personas.length > 0 ? (
                      <CouncilPanel state={council} />
                    ) : browser.visible ? (
                      <BrowserPanel state={browser} />
                    ) : (
                      <AgentStatus narration={narration} actionCount={cascade.rows.length} />
                    )}
                  </motion.div>
                )}

                {/* COMPLETE — celebration */}
                {view === "complete" && summary && (
                  <motion.div
                    key="done"
                    layout
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.96 }}
                    transition={{ duration: 0.45, ease: PUNCTUAL_EASE }}
                    className="m-auto"
                  >
                    <CompletionCard summary={summary} onRestart={resetAll} />
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>

          </div>
        </main>

        {/* Narration footer — fixed height, never scrolls */}
        <div className="h-12 border-t border-border/70 bg-background/80 backdrop-blur flex items-center px-6">
          <div className="flex items-center gap-3 max-w-3xl mx-auto w-full">
            <span className={cn(
              "inline-block w-2 h-2 rounded-full",
              narration ? "bg-status-complete animate-pulse" : "bg-muted",
            )} />
            <span className="label-uc text-muted-foreground">Voice</span>
            <AnimatePresence mode="wait">
              {narration ? (
                <motion.span
                  key={narration}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.28, ease: PUNCTUAL_EASE }}
                  className="text-body text-foreground italic flex-1 truncate"
                >
                  {narration}
                </motion.span>
              ) : (
                <span className="text-body text-muted-foreground italic flex-1">— quiet —</span>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* Floating restart mic during running */}
        <AnimatePresence>
          {view !== "idle" && view !== "complete" && (
            <motion.div
              key="float-mic"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={{ duration: 0.3, ease: PUNCTUAL_EASE }}
              className="fixed bottom-16 right-6 z-30"
            >
              <Button
                onClick={onMicClick}
                disabled={!health?.user_id}
                size="icon"
                aria-label="Start a new mission"
                className="w-12 h-12 rounded-full shadow-[0_8px_24px_rgba(74,90,122,0.25)]"
              >
                <Mic className="w-4 h-4" />
              </Button>
            </motion.div>
          )}
        </AnimatePresence>

        <MicDialog
          open={micOpen}
          state={mic.state}
          seconds={mic.seconds}
          analyser={mic.analyser.current}
          onStop={mic.stop}
          onCancel={onMicCancel}
        />

        <MicDialog
          open={voteOpen}
          state={confirmMic.state}
          seconds={confirmMic.seconds}
          analyser={confirmMic.analyser.current}
          onStop={confirmMic.stop}
          onCancel={onConfirmCancel}
        />
      </div>
    </MotionConfig>
  );
}

// =================== sub-components ===================
function IdleMic({
  onStart,
  onTripStart,
  disabled,
  muted,
  hint,
}: {
  onStart: () => void;
  onTripStart?: () => void;
  disabled?: boolean;
  muted?: boolean;
  hint?: string;
}) {
  return (
    <div className="relative flex flex-col items-center">
      <div className="pointer-events-none absolute inset-0 -m-32 rounded-full opacity-[0.10] blur-3xl bg-[radial-gradient(closest-side,var(--color-punctual),transparent)]" />

      <motion.div
        animate={{ scale: [1, 1.04, 1] }}
        transition={{ duration: 3.4, ease: "easeInOut", repeat: Infinity }}
        className="relative"
      >
        <span className="absolute inset-0 -m-3 rounded-full border border-punctual/15 animate-ping [animation-duration:3s]" />
        <span className="absolute inset-0 -m-2 rounded-full border border-punctual/25 animate-ping [animation-duration:4s] [animation-delay:1.5s]" />
        <Button
          size="xl"
          onClick={onStart}
          disabled={disabled}
          className="relative w-24 h-24 shadow-[0_0_0_1px_rgba(255,255,255,0.06),0_18px_36px_rgba(74,90,122,0.35)] hover:scale-[1.04] active:scale-[0.97] transition-transform duration-default ease-[cubic-bezier(0.2,0,0,1)]"
        >
          <Mic className="w-9 h-9" />
        </Button>
      </motion.div>

      <h1 className="text-title-lg text-foreground mt-9 mb-1.5">
        {muted ? (hint || "Preparing the room…") : "Tap to talk"}
      </h1>
      <p className="text-body text-muted-foreground mb-3">
        {muted
          ? "We're seeding your accounts and assembling the council. The mic unlocks when they're ready."
          : "Speak the mission like you'd say it to a friend."}
      </p>

      {!muted && onTripStart && (
        <button
          type="button"
          onClick={onTripStart}
          disabled={disabled}
          className="text-meta text-punctual hover:text-foreground transition-colors mb-6 underline-offset-4 hover:underline disabled:opacity-40"
        >
          or chat with the Trip Agent →
        </button>
      )}

      {!muted && (
        <div className="flex flex-col items-center gap-1">
          {[
            "should I buy this hundred-and-twenty-euro sweater?",
            "five hundred for me and Sara, weekend",
            "flying to Tokyo Friday, freeze the card",
          ].map((s, i) => (
            <motion.span
              key={s}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 + i * 0.08, duration: 0.32, ease: PUNCTUAL_EASE }}
              className="text-meta text-paper-400 italic"
            >
              “{s}”
            </motion.span>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentStatus({ narration, actionCount }: { narration: string | null; actionCount: number }) {
  return (
    <Card className="h-full flex flex-col p-6 justify-center items-center text-center gap-4">
      <div className="label-uc text-muted-foreground">Agent</div>
      <motion.div
        animate={{ scale: [1, 1.05, 1] }}
        transition={{ duration: 2.6, ease: "easeInOut", repeat: Infinity }}
        className="w-16 h-16 rounded-full bg-punctual/15 grid place-items-center"
      >
        <span className="w-3 h-3 rounded-full bg-punctual animate-pulse" />
      </motion.div>
      <AnimatePresence mode="wait">
        <motion.div
          key={narration || "thinking"}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.32, ease: PUNCTUAL_EASE }}
          className="text-title text-foreground max-w-md italic"
        >
          {narration ? `“${narration}”` : "Thinking…"}
        </motion.div>
      </AnimatePresence>
      <div className="text-meta tabular text-muted-foreground mt-2">
        {actionCount} step{actionCount === 1 ? "" : "s"} on the cascade
      </div>
    </Card>
  );
}

function CompletionCard({ summary, onRestart }: { summary: string; onRestart: () => void }) {
  return (
    <Card className="px-10 py-9 text-center max-w-lg">
      <motion.div
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        className="w-14 h-14 rounded-full bg-status-complete/15 grid place-items-center mx-auto mb-4"
      >
        <span className="w-7 h-7 rounded-full bg-status-complete grid place-items-center text-paper-50 text-meta font-bold">✓</span>
      </motion.div>
      <div className="label-uc text-muted-foreground mb-2">Mission complete</div>
      <p className="text-title text-foreground leading-snug">{summary}</p>
      <Button variant="ghost" size="sm" onClick={onRestart} className="mt-5 text-muted-foreground hover:text-foreground">
        ← Start another
      </Button>
    </Card>
  );
}
