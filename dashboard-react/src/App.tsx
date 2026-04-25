import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Camera, Mic, Sparkles, Wifi, WifiOff } from "lucide-react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CascadeRow } from "@/components/CascadeRow";
import { MicDialog } from "@/components/MicDialog";
import { CameraDialog } from "@/components/CameraDialog";
import { BrowserPanel, type BrowserPanelState } from "@/components/BrowserPanel";
import { SourceSidebar, SOURCES_BY_MISSION } from "@/components/SourceSidebar";
import { AnimatedNumber } from "@/components/AnimatedNumber";
import { useEventBus } from "@/hooks/useEventBus";
import { useMicRecorder } from "@/hooks/useMicRecorder";
import { audioQueue, fxBuzz, fxChime, fxDoneStep, fxTick, fxZoom } from "@/lib/audio-fx";
import { cn, fmtEur } from "@/lib/utils";
import type { BusEvent, CascadeRow as Row, HealthInfo, TaxExtraction } from "@/lib/types";

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
type ViewState = "idle" | "voice" | "running" | "complete";

export function App() {
  const [cascade, dispatch] = useReducer(cascadeReducer, { rows: [], inFlight: [] });
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [voiceCard, setVoiceCard] = useState<{ label: string; line: string; route?: string } | null>(null);
  const [browser, setBrowser] = useState<BrowserPanelState>({ visible: false, line: "", step: "step 0", shotData: null, changing: false });
  const [draft, setDraft] = useState<{ status: "pending" | "accepted" | "rejected" | "timeout"; title: string; msg: string } | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [narration, setNarration] = useState<string | null>(null);
  const [micOpen, setMicOpen] = useState(false);
  const [view, setView] = useState<ViewState>("idle");
  // Sustainability donation prompt at the end of every mission.
  const [donationPrompt, setDonationPrompt] = useState<{
    prompt_line: string; amount_eur: number; total_spent_eur: number; cause: string;
    totalSeconds: number; startedAt: number;
  } | null>(null);
  const [donationDecision, setDonationDecision] = useState<{
    transcript: string; decision: string;
  } | null>(null);
  const [donateOpen, setDonateOpen] = useState(false);
  // Tax invoice scanner.
  const [cameraOpen, setCameraOpen] = useState(false);
  const [taxScanState, setTaxScanState] = useState<"idle" | "scanning" | "scanned" | "error">("idle");
  const [taxScanMessage, setTaxScanMessage] = useState<string | null>(null);
  const [taxExtracted, setTaxExtracted] = useState<TaxExtraction | null>(null);
  const [taxAwaiting, setTaxAwaiting] = useState<{ question: string; iban: string; amount_eur: number; recipient: string } | null>(null);
  const [taxConfirmOpen, setTaxConfirmOpen] = useState(false);
  // Active mission name (drives the SourceSidebar) + how many sources have
  // "lit up" so far (one per completed step, capped to the source-set length).
  const [currentMission, setCurrentMission] = useState<string | null>(null);
  const [sourceHighlight, setSourceHighlight] = useState(0);
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

  // Second recorder dedicated to the donation yes/no — uploads to a different
  // endpoint with no extra fields.
  const donationMic = useMicRecorder({
    maxDurationSec: 10,
    uploadFn: async (blob, mime) => {
      const ext = mime.includes("webm") ? "webm" : mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "bin";
      const fd = new FormData();
      fd.append("audio", blob, `donate.${ext}`);
      const r = await fetch("/missions/donate/confirm", { method: "POST", body: fd });
      return r.json();
    },
    onTranscribed: (resp) => {
      setDonateOpen(false);
      if (!resp.ok) alert("Donation reply upload failed: " + (resp.error || "unknown"));
    },
  });
  const onDonateCancel = useCallback(() => { donationMic.cancel(); setDonateOpen(false); }, [donationMic]);

  // Recorder for the tax-invoice confirmation prompt — uploads to its own
  // endpoint so the server's bridge state doesn't get crossed with donations.
  const taxMic = useMicRecorder({
    maxDurationSec: 10,
    uploadFn: async (blob, mime) => {
      const ext = mime.includes("webm") ? "webm" : mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "bin";
      const fd = new FormData();
      fd.append("audio", blob, `tax-confirm.${ext}`);
      const r = await fetch("/missions/tax/confirm", { method: "POST", body: fd });
      return r.json();
    },
    onTranscribed: (resp) => {
      setTaxConfirmOpen(false);
      if (!resp.ok) alert("Tax confirm upload failed: " + (resp.error || "unknown"));
    },
  });
  const onTaxConfirmCancel = useCallback(() => { taxMic.cancel(); setTaxConfirmOpen(false); }, [taxMic]);

  const onCameraOpen = useCallback(() => {
    setTaxScanState("idle");
    setTaxScanMessage(null);
    setTaxExtracted(null);
    setTaxAwaiting(null);
    setCameraOpen(true);
  }, []);

  const onCameraCancel = useCallback(() => { setCameraOpen(false); }, []);

  const onCameraCapture = useCallback(async (blob: Blob) => {
    setTaxScanState("scanning");
    setTaxScanMessage("Reading the invoice…");
    try {
      const fd = new FormData();
      fd.append("image", blob, "receipt.jpg");
      const r = await fetch("/missions/tax/scan", { method: "POST", body: fd });
      const j = await r.json();
      if (!j.ok) {
        setTaxScanState("error");
        setTaxScanMessage("Scan failed: " + (j.error || "unknown"));
        return;
      }
      // Server will fire `tax_extracted` and `awaiting_tax_confirm` shortly.
      setTaxScanMessage("Got the photo — extracting fields…");
    } catch (e: unknown) {
      setTaxScanState("error");
      const msg = e instanceof Error ? e.message : "upload failed";
      setTaxScanMessage("Upload failed: " + msg);
    }
  }, []);

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
    setDraft(null);
    setSummary(null);
    setDonationPrompt(null);
    setDonationDecision(null);
    setTaxScanState("idle");
    setTaxScanMessage(null);
    setTaxExtracted(null);
    setTaxAwaiting(null);
    setCurrentMission(null);
    setSourceHighlight(0);
    audioQueue.reset();
    setView("idle");
  }, []);

  const handleEvent = useCallback((ev: BusEvent) => {
    switch (ev.type) {
      case "mission_started":
        dispatch({ type: "reset" });
        setVoiceCard(null);
        setBrowser({ visible: false, line: "", step: "step 0", shotData: null, changing: false });
        setDraft(null);
        setSummary(null);
        setDonationPrompt(null);
        setDonationDecision(null);
        setSourceHighlight(0);
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
      case "step_finished":
        fxDoneStep();
        dispatch({ type: "finished", tool: String(ev.tool), result: (ev.result as Record<string, unknown>) || {} });
        // Light up one more source card per completed step.
        setSourceHighlight((n) => n + 1);
        break;
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
        if (typeof ev.mission === "string") setCurrentMission(ev.mission);
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
      case "awaiting_donation": {
        const totalSeconds = Math.max(3, Number(ev.timeout_s) || 22);
        setDonationPrompt({
          prompt_line:     String(ev.prompt_line || "Round up to a sustainability cause?"),
          amount_eur:      Number(ev.amount_eur || 0),
          total_spent_eur: Number(ev.total_spent_eur || 0),
          cause:           String(ev.cause || "Trees for All"),
          totalSeconds,
          startedAt:       Date.now(),
        });
        setDonationDecision(null);
        // Wait for the agent's TTS to finish playing before opening the mic
        // — otherwise the mic captures the agent's own voice as input.
        // 300 ms extra buffer for trailing room reverb / network jitter.
        audioQueue.waitForDrain().then(() => {
          setTimeout(() => {
            setDonateOpen(true);
            void donationMic.start();
          }, 300);
        });
        break;
      }
      case "donation_decision_received": {
        setDonationDecision({
          transcript: String(ev.transcript || ""),
          decision:   String(ev.decision || "unsure"),
        });
        setDonationPrompt(null);
        setDonateOpen(false);
        break;
      }
      case "donation_decision_timeout": {
        setDonationDecision({ transcript: "(no answer)", decision: "timeout" });
        setDonationPrompt(null);
        setDonateOpen(false);
        break;
      }
      case "tax_scan_started": {
        setTaxScanState("scanning");
        setTaxScanMessage("Reading the invoice…");
        setCurrentMission("tax");
        setSourceHighlight(0);
        setView("running");
        break;
      }
      case "tax_extracted": {
        const ex: TaxExtraction = {
          iban:        typeof ev.iban === "string" ? ev.iban : null,
          bic:         typeof ev.bic === "string" ? ev.bic : null,
          recipient:   typeof ev.recipient === "string" ? ev.recipient : null,
          amount_eur:  typeof ev.amount_eur === "number" ? ev.amount_eur : null,
          description: typeof ev.description === "string" ? ev.description : null,
        };
        setTaxExtracted(ex);
        setTaxScanState("scanned");
        setTaxScanMessage(null);
        // Close the camera — the next interaction is a voice yes/no.
        setCameraOpen(false);
        break;
      }
      case "tax_scan_error": {
        setTaxScanState("error");
        setTaxScanMessage(String(ev.error || "Scan failed"));
        break;
      }
      case "awaiting_tax_confirm": {
        setTaxAwaiting({
          question:   String(ev.question || "Pay this invoice?"),
          iban:       String(ev.iban || ""),
          amount_eur: Number(ev.amount_eur || 0),
          recipient:  String(ev.recipient || "—"),
        });
        // Wait for the agent's TTS to finish playing before opening the mic.
        audioQueue.waitForDrain().then(() => {
          setTimeout(() => {
            setTaxConfirmOpen(true);
            void taxMic.start();
          }, 300);
        });
        break;
      }
      case "tax_confirm_received": {
        setTaxAwaiting(null);
        setTaxConfirmOpen(false);
        break;
      }
      case "tax_payment_complete":
      case "tax_payment_skipped":
      case "tax_payment_error": {
        setTaxAwaiting(null);
        setTaxConfirmOpen(false);
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
  }, [donationMic, taxMic]);

  const status = useEventBus(handleEvent);

  useEffect(() => {
    let cancelled = false;
    fetch("/health").then((r) => r.json()).then((j: HealthInfo) => { if (!cancelled) setHealth(j); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const draftBadgeVariant = useMemo(() => {
    if (!draft) return "outline" as const;
    if (draft.status === "accepted") return "complete" as const;
    if (draft.status === "rejected" || draft.status === "timeout") return "overdue" as const;
    return "upcoming" as const;
  }, [draft]);

  const splitView = view === "running" || (view === "voice" && (browser.visible || cascade.rows.length > 0)) || !!taxExtracted || taxScanState === "scanning";
  const showSources = !!currentMission && !!SOURCES_BY_MISSION[currentMission as keyof typeof SOURCES_BY_MISSION] && splitView;

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
            !splitView && "grid-cols-1",
            splitView && !showSources && "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]",
            splitView && showSources  && "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)_minmax(0,320px)]",
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
                    {(taxExtracted || taxScanState === "scanning") && (
                      <motion.div key="tax-extracted" layout {...FADE_UP}>
                        <Card className="p-4 border-l-2 border-l-status-scheduled bg-status-scheduled/5">
                          <div className="flex items-center gap-2 mb-1.5">
                            <Camera className="w-3.5 h-3.5 text-status-scheduled" />
                            <span className="label-uc text-status-scheduled">Tax invoice scan</span>
                            {taxAwaiting && (
                              <span className="ml-auto label-uc text-muted-foreground animate-pulse">awaiting voice</span>
                            )}
                          </div>
                          {taxScanState === "scanning" && !taxExtracted && (
                            <div className="text-body text-muted-foreground italic">Reading the invoice…</div>
                          )}
                          {taxExtracted && (
                            <div className="space-y-1 text-body">
                              <div className="flex justify-between gap-3">
                                <span className="text-muted-foreground">Recipient</span>
                                <span className="text-foreground truncate">{taxExtracted.recipient || "—"}</span>
                              </div>
                              <div className="flex justify-between gap-3">
                                <span className="text-muted-foreground">Amount</span>
                                <span className="text-foreground tabular font-medium">
                                  {taxExtracted.amount_eur != null ? fmtEur(taxExtracted.amount_eur) : "—"}
                                </span>
                              </div>
                              <div className="flex justify-between gap-3">
                                <span className="text-muted-foreground">IBAN</span>
                                <span className="text-foreground tabular font-mono text-meta truncate">{taxExtracted.iban || "—"}</span>
                              </div>
                              {taxExtracted.bic && (
                                <div className="flex justify-between gap-3">
                                  <span className="text-muted-foreground">BIC</span>
                                  <span className="text-foreground tabular font-mono text-meta">{taxExtracted.bic}</span>
                                </div>
                              )}
                              {taxExtracted.description && (
                                <div className="flex justify-between gap-3">
                                  <span className="text-muted-foreground">Reference</span>
                                  <span className="text-foreground truncate">{taxExtracted.description}</span>
                                </div>
                              )}
                            </div>
                          )}
                          {taxAwaiting && (
                            <div className="mt-3 pt-3 border-t border-border/70 italic text-foreground">"{taxAwaiting.question}"</div>
                          )}
                        </Card>
                      </motion.div>
                    )}
                    {donationPrompt && (
                      <motion.div key="donation-prompt" layout {...FADE_UP}>
                        <Card className="p-4 border-l-2 border-l-status-complete bg-status-complete/5">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="relative w-2 h-2">
                              <span className="absolute inset-0 rounded-full bg-status-complete animate-ping opacity-75" />
                              <span className="absolute inset-0 rounded-full bg-status-complete" />
                            </span>
                            <span className="label-uc text-status-complete">Sustainability · listening</span>
                            <span className="ml-auto label-uc tabular text-muted-foreground">
                              €{donationPrompt.amount_eur.toFixed(0)} → {donationPrompt.cause}
                            </span>
                          </div>
                          <div className="text-title text-foreground leading-snug italic">"{donationPrompt.prompt_line}"</div>
                          <div className="text-meta tabular text-muted-foreground mt-1">
                            {((donationPrompt.amount_eur / Math.max(donationPrompt.total_spent_eur, 0.01)) * 100).toFixed(1)}% of €{donationPrompt.total_spent_eur.toFixed(0)} spent today
                          </div>
                        </Card>
                      </motion.div>
                    )}
                    {donationDecision && !donationPrompt && (
                      <motion.div key="donation-echo" layout {...FADE_UP}>
                        <Card className={cn(
                          "p-3 flex items-center gap-3 border-l-2",
                          donationDecision.decision === "yes" && "border-l-status-complete",
                          donationDecision.decision === "no" && "border-l-paper-400",
                          donationDecision.decision === "timeout" && "border-l-status-overdue",
                          donationDecision.decision === "unsure" && "border-l-status-upcoming",
                        )}>
                          <Badge className="shrink-0 uppercase">{donationDecision.decision}</Badge>
                          <div className="text-body text-foreground italic truncate">"{donationDecision.transcript}"</div>
                        </Card>
                      </motion.div>
                    )}
                  </AnimatePresence>

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
                {/* IDLE — centered breathing mic */}
                {view === "idle" && (
                  <motion.div
                    key="hero"
                    layout
                    {...FADE_UP}
                    className="m-auto flex flex-col items-center"
                  >
                    <IdleMic onStart={onMicClick} disabled={!health?.user_id} />
                  </motion.div>
                )}

                {/* RUNNING / VOICE — browser panel if available, else agent status */}
                {(view === "running" || view === "voice") && (
                  <motion.div
                    key="right"
                    layout
                    {...FADE_UP}
                    className="w-full h-full"
                  >
                    {browser.visible ? (
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

            {/* Third column — Sources sidebar (mission-aware) */}
            <AnimatePresence mode="wait">
              {showSources && (
                <motion.div
                  key={`sources-${currentMission}`}
                  layout
                  initial={{ opacity: 0, x: 16 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 16 }}
                  transition={{ duration: 0.34, ease: PUNCTUAL_EASE }}
                  className="min-h-0 hidden lg:block"
                >
                  <SourceSidebar mission={currentMission} highlightCount={sourceHighlight} />
                </motion.div>
              )}
            </AnimatePresence>

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
              {narration && (
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
              className="fixed bottom-16 right-6 z-30 flex flex-col gap-3"
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
              <Button
                onClick={onCameraOpen}
                disabled={!health?.user_id}
                size="icon"
                variant="outline"
                aria-label="Scan a tax invoice"
                className="w-12 h-12 rounded-full shadow-[0_8px_24px_rgba(74,90,122,0.25)]"
              >
                <Camera className="w-4 h-4" />
              </Button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Always-available camera button on the idle screen too */}
        {view === "idle" && (
          <div className="fixed bottom-16 right-6 z-30 flex flex-col gap-3">
            <Button
              onClick={onCameraOpen}
              disabled={!health?.user_id}
              size="icon"
              variant="outline"
              aria-label="Scan a tax invoice"
              className="w-12 h-12 rounded-full shadow-[0_8px_24px_rgba(74,90,122,0.25)]"
            >
              <Camera className="w-4 h-4" />
            </Button>
          </div>
        )}

        <MicDialog
          open={micOpen}
          state={mic.state}
          seconds={mic.seconds}
          analyser={mic.analyser.current}
          onStop={mic.stop}
          onCancel={onMicCancel}
        />

        <MicDialog
          open={donateOpen}
          state={donationMic.state}
          seconds={donationMic.seconds}
          analyser={donationMic.analyser.current}
          onStop={donationMic.stop}
          onCancel={onDonateCancel}
        />

        <MicDialog
          open={taxConfirmOpen}
          state={taxMic.state}
          seconds={taxMic.seconds}
          analyser={taxMic.analyser.current}
          onStop={taxMic.stop}
          onCancel={onTaxConfirmCancel}
        />

        <CameraDialog
          open={cameraOpen}
          onCapture={onCameraCapture}
          onCancel={onCameraCancel}
          status={taxScanState}
          message={taxScanMessage}
        />
      </div>
    </MotionConfig>
  );
}

// =================== sub-components ===================
function IdleMic({ onStart, disabled }: { onStart: () => void; disabled?: boolean }) {
  return (
    <div className="relative flex flex-col items-center">
      {/* Layered colour aurora — coral, violet, sky in a soft tri-radial wash */}
      <div
        className="pointer-events-none absolute inset-0 -m-40 rounded-full opacity-60 blur-3xl"
        style={{
          background:
            "radial-gradient(circle at 30% 30%, rgba(255,111,143,0.35) 0%, transparent 55%)," +
            "radial-gradient(circle at 70% 35%, rgba(183,136,255,0.30) 0%, transparent 55%)," +
            "radial-gradient(circle at 50% 80%, rgba(105,179,255,0.30) 0%, transparent 55%)",
        }}
      />
      {/* Slow-rotating gradient ring underneath the button */}
      <div
        className="pointer-events-none absolute -inset-6 rounded-full opacity-90"
        style={{
          background:
            "conic-gradient(from 0deg, var(--color-accent-coral), var(--color-accent-violet), var(--color-accent-sky), var(--color-accent-mint), var(--color-accent-coral))",
          mask: "radial-gradient(circle, transparent 56%, black 60%, black 70%, transparent 73%)",
          WebkitMask: "radial-gradient(circle, transparent 56%, black 60%, black 70%, transparent 73%)",
          animation: "gradientSpin 12s linear infinite",
          filter: "blur(0.5px)",
        }}
      />

      <motion.div
        animate={{ scale: [1, 1.04, 1] }}
        transition={{ duration: 3.4, ease: "easeInOut", repeat: Infinity }}
        className="relative"
      >
        <span className="absolute inset-0 -m-3 rounded-full border border-accent-coral/30 animate-ping [animation-duration:3s]" />
        <span className="absolute inset-0 -m-2 rounded-full border border-accent-violet/40 animate-ping [animation-duration:4s] [animation-delay:1.5s]" />
        <Button
          variant="glow"
          size="xl"
          onClick={onStart}
          disabled={disabled}
          className="relative w-28 h-28 shadow-[0_0_0_1px_rgba(255,255,255,0.10),0_24px_48px_-12px_rgba(255,111,143,0.5)]"
        >
          <Mic className="w-10 h-10" />
        </Button>
      </motion.div>

      <h1 className="text-title-lg mt-9 mb-1.5 gradient-text font-semibold">Tap to talk</h1>
      <p className="text-body text-muted-foreground mb-7">Speak the mission like you'd say it to a friend.</p>

      <div className="flex flex-col items-center gap-1">
        {[
          "five hundred for me and Sara, weekend",
          "lock in this month's bills",
          "flying to Tokyo Friday, freeze the card",
        ].map((s, i) => (
          <motion.span
            key={s}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 + i * 0.08, duration: 0.32, ease: PUNCTUAL_EASE }}
            whileHover={{ y: -2, color: "rgb(255 111 143)" }}
            className="text-meta text-paper-400 italic cursor-default transition-colors"
          >
            “{s}”
          </motion.span>
        ))}
      </div>
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
