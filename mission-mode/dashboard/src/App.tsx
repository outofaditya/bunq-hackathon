import { useEffect, useRef, useState } from "react";
import Chat from "./Chat";
import Dashboard from "./Dashboard";
import { AnimatedNumber } from "./AnimatedNumber";
import { audioQueue, fxBuzz, fxChime, fxDoneStep, fxTick } from "./audio-fx";
import type { ChatEntry, Phase, ServerEvent, TileName, TileState } from "./types";

type ConnectionStatus = "connecting" | "live" | "reconnecting";

const INITIAL_TILES: TileState[] = [
  { name: "create_sub_account", label: "Sub-account", status: "idle" },
  { name: "fund_sub_account", label: "Funded", status: "idle" },
  { name: "pay_vendor", label: "Hotel paid", status: "idle" },
  { name: "create_draft_payment", label: "Dinner approval", status: "idle" },
  { name: "schedule_recurring", label: "Weekly savings", status: "idle" },
  { name: "request_from_partner", label: "Split requested", status: "idle" },
  { name: "send_slack", label: "Slack sent", status: "idle" },
];

export default function App() {
  const [entries, setEntries] = useState<ChatEntry[]>([
    { kind: "agent", text: "Hi. I'm your trip agent. Describe the trip you'd like and I'll plan it.", streaming: false },
  ]);
  const [phase, setPhase] = useState<Phase>("UNDERSTANDING");
  const [tiles, setTiles] = useState<TileState[]>(INITIAL_TILES);
  const [balance, setBalance] = useState<{ goal: number; value: number; name: string } | null>(null);
  const [lastNarration, setLastNarration] = useState<string>("");
  const [browserFrame, setBrowserFrame] = useState<string | null>(null);
  const [browserStatus, setBrowserStatus] = useState<{ status: string; step?: string; hotel?: string; booking_ref?: string; query?: string } | null>(null);
  const [searchFeed, setSearchFeed] = useState<{ query: string; results: { title: string; url: string; snippet: string }[] }[]>([]);
  const [primaryBalance, setPrimaryBalance] = useState<number | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("connecting");
  const sseRef = useRef<EventSource | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Mount SSE once
  useEffect(() => {
    const es = new EventSource("/events");
    sseRef.current = es;
    es.onopen = () => setConnection("live");
    es.onerror = () => setConnection("reconnecting");
    es.onmessage = (e) => {
      let evt: ServerEvent;
      try {
        evt = JSON.parse(e.data);
      } catch {
        return;
      }
      handleEvent(evt);
    };
    return () => es.close();
  }, []);

  function handleEvent(evt: ServerEvent) {
    switch (evt.type) {
      case "user_message":
        // Handled optimistically in sendMessage(); no-op here so we don't
        // double-render if any code path ever republishes it.
        break;

      case "agent_text_delta":
        setEntries((xs) => {
          const last = xs[xs.length - 1];
          if (last && last.kind === "agent" && last.streaming) {
            return [...xs.slice(0, -1), { ...last, text: last.text + evt.text }];
          }
          return [...xs, { kind: "agent", text: evt.text, streaming: true }];
        });
        break;

      case "agent_message":
        setEntries((xs) => {
          const last = xs[xs.length - 1];
          if (last && last.kind === "agent" && last.streaming) {
            return [...xs.slice(0, -1), { ...last, text: evt.text, streaming: false }];
          }
          return [...xs, { kind: "agent", text: evt.text, streaming: false }];
        });
        break;

      case "tool_call":
        // Sound effects: subtle tick on firing, soft confirm on ok, low buzz on failure.
        if (evt.status === "firing") fxTick();
        else if (evt.status === "ok") fxDoneStep();
        else if (evt.status === "failed") fxBuzz();
        setEntries((xs) => [
          ...xs,
          { kind: "tool", name: evt.name, status: evt.status, input: evt.input, result: evt.result, error: evt.error },
        ]);
        if (evt.name in tileByName) {
          setTiles((ts) => {
            const name = evt.name as TileName;
            // Draft-payment is special: after the tool call, stay "pending" until webhook ACCEPTED
            const tileStatus: TileState["status"] =
              evt.name === "create_draft_payment" && evt.status === "ok" ? "pending" : evt.status;
            const updated: TileState = {
              ...ts.find((t) => t.name === name)!,
              status: tileStatus,
              detail: detailFor(evt.name, evt.result),
            };
            return ts.map((t) => (t.name === name ? updated : t));
          });
          if (evt.name === "create_sub_account" && evt.status === "ok" && evt.result) {
            const r = evt.result as { name: string; goal_eur: number };
            setBalance({ goal: r.goal_eur, value: 0, name: r.name });
          }
          if (evt.name === "fund_sub_account" && evt.status === "ok" && evt.result) {
            const r = evt.result as { amount_eur: number };
            setBalance((b) => (b ? { ...b, value: r.amount_eur } : b));
          }
          if (evt.name === "pay_vendor" && evt.status === "ok" && evt.result) {
            const r = evt.result as { amount_eur: number };
            setBalance((b) => (b ? { ...b, value: Math.max(0, b.value - r.amount_eur) } : b));
          }
        }
        break;

      case "draft_payment_event":
        // User tapped approve/reject on their bunq app
        if (evt.status === "ACCEPTED") {
          setTiles((ts) =>
            ts.map((t) =>
              t.name === "create_draft_payment"
                ? { ...t, status: "ok", detail: `${t.detail} · approved ✓` }
                : t,
            ),
          );
        } else if (evt.status === "REJECTED") {
          setTiles((ts) =>
            ts.map((t) =>
              t.name === "create_draft_payment" ? { ...t, status: "failed", detail: "rejected" } : t,
            ),
          );
        }
        break;

      case "payment_event":
      case "schedule_event":
      case "request_event":
      case "bunq_webhook":
        // Webhooks currently only used for the draft-approval flash. Other events are informational.
        break;

      case "options":
        setEntries((xs) => [
          ...xs,
          {
            kind: "options",
            intro: evt.intro,
            options: evt.options.map((o) => ({ ...o, image_status: "loading" as const })),
          },
        ]);
        break;

      case "option_image":
        setEntries((xs) =>
          xs.map((e) => {
            if (e.kind !== "options") return e;
            const idx = e.options.findIndex((o) => o.id === evt.option_id);
            if (idx === -1) return e;
            const next = e.options.slice();
            next[idx] = {
              ...next[idx],
              image_url: evt.image_url ?? null,
              image_status: evt.status,
            };
            return { ...e, options: next };
          }),
        );
        break;

      case "confirmation_request":
        setEntries((xs) => [...xs, { kind: "confirmation", summary: evt.summary }]);
        break;

      case "narration":
        setEntries((xs) => [...xs, { kind: "narration", text: evt.text }]);
        setLastNarration(evt.text);
        // Stream TTS audio via the shared sequential queue (no overlapping clips).
        audioQueue.enqueue(`/tts?text=${encodeURIComponent(evt.text)}`);
        break;

      case "phase":
        setPhase(evt.value);
        // Mission-complete chord on DONE.
        if (evt.value === "DONE") fxChime();
        break;

      case "balance":
        setBalance((b) => (b ? { ...b, value: evt.value_eur } : b));
        break;

      case "balance_snapshot":
        setPrimaryBalance(evt.primary_balance_eur);
        break;

      case "browser_frame":
        setBrowserFrame(evt.jpeg_b64);
        break;

      case "browser_status":
        setBrowserStatus({ status: evt.status, step: evt.step, hotel: evt.hotel, booking_ref: evt.booking_ref, query: evt.query });
        // Clear the frame once the browser is done so the panel doesn't freeze forever.
        if (evt.status === "done") {
          setTimeout(() => {
            setBrowserFrame(null);
            setBrowserStatus(null);
          }, 3000);
        }
        break;

      case "search_results":
        setSearchFeed((f) => [...f, { query: evt.query, results: evt.results }]);
        break;
    }
  }

  function sendMessage(text: string) {
    // Optimistic UI: show the user bubble immediately. The server no longer
    // re-broadcasts user_message over SSE (would double-render), so this is
    // the sole source of the user bubble.
    setEntries((xs) => [...xs, { kind: "user", text }]);
    fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionIdRef.current }),
    })
      .then((r) => r.json())
      .then((j) => {
        if (j?.session_id) {
          sessionIdRef.current = j.session_id;
          setSessionId(j.session_id);
        }
      })
      .catch(() => {});
  }

  function selectOption(entryIdx: number, optionId: string) {
    setEntries((xs) => {
      const e = xs[entryIdx];
      if (e.kind !== "options") return xs;
      return xs.map((x, i) => (i === entryIdx ? { ...e, selected: optionId } : x));
    });
    const e = entries[entryIdx];
    if (e.kind !== "options") return;
    const picked = e.options.find((o) => o.id === optionId);
    if (!picked) return;
    sendMessage(`I'll take ${optionId} — ${picked.hotel} with ${picked.restaurant} at €${picked.total_eur}.`);
  }

  function confirm(entryIdx: number, answer: "yes" | "no") {
    setEntries((xs) => {
      const e = xs[entryIdx];
      if (e.kind !== "confirmation") return xs;
      return xs.map((x, i) => (i === entryIdx ? { ...e, answered: true } : x));
    });
    sendMessage(answer === "yes" ? "yes, go" : "no, cancel");
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">bunq</span>
          <span className="brand-divider" />
          <div className="brand-product">
            <span className="brand-product-name">Trip Agent</span>
            <span className="brand-tagline">bank of The Free</span>
          </div>
        </div>
        <div className="spacer" />
        {primaryBalance !== null && (
          <div className="primary-balance" title="Primary account balance">
            <span className="primary-balance-label">Primary</span>
            <AnimatedNumber
              value={primaryBalance}
              format={(n) => `€${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              className="primary-balance-amount tabular"
            />
          </div>
        )}
        <ConnectionIndicator status={connection} />
      </header>
      <main className="app-body">
        <Chat
          entries={entries}
          onSend={sendMessage}
          onSelectOption={selectOption}
          onConfirm={confirm}
          phase={phase}
        />
        <Dashboard
          tiles={tiles}
          balance={balance}
          narration={lastNarration}
          sessionId={sessionId}
          browserFrame={browserFrame}
          browserStatus={browserStatus}
          searchFeed={searchFeed}
        />
      </main>
      <div className="rainbow-strip" aria-hidden="true" />
    </div>
  );
}

const tileByName: Record<string, true> = {
  create_sub_account: true,
  fund_sub_account: true,
  pay_vendor: true,
  create_draft_payment: true,
  schedule_recurring: true,
  request_from_partner: true,
  send_slack: true,
};

function detailFor(name: string, result: unknown): string | undefined {
  if (!result || typeof result !== "object") return undefined;
  const r = result as Record<string, unknown>;
  if (name === "create_sub_account") return `€${r.goal_eur} goal`;
  if (name === "fund_sub_account") return `€${r.amount_eur} moved`;
  if (name === "pay_vendor") return `€${r.amount_eur} · ${r.vendor}`;
  if (name === "create_draft_payment") return `€${r.amount_eur} · awaiting tap`;
  if (name === "schedule_recurring") return `€${r.amount_eur}/week`;
  if (name === "request_from_partner") return `€${r.amount_eur} from ${r.partner}`;
  if (name === "send_slack") return (r as any).ok ? "delivered" : "webhook unset";
  return undefined;
}

function ConnectionIndicator({ status }: { status: ConnectionStatus }) {
  const label = status === "live" ? "Live" : status === "reconnecting" ? "Reconnecting" : "Connecting";
  return (
    <div className={`conn-indicator conn-${status}`} title={`SSE: ${status}`}>
      {status === "live" ? <WifiOnIcon /> : <WifiOffIcon />}
      <span className="conn-label">{label}</span>
    </div>
  );
}

function WifiOnIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2 9.5a16 16 0 0 1 20 0" />
      <path d="M5.5 13a11 11 0 0 1 13 0" />
      <path d="M9 16.5a6 6 0 0 1 6 0" />
      <circle cx="12" cy="20" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

function WifiOffIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 3l18 18" />
      <path d="M16.7 13.7a8 8 0 0 0-9.4 0" />
      <path d="M2 9.5a16 16 0 0 1 6.5-3.5" />
      <path d="M15.5 6a16 16 0 0 1 6.5 3.5" />
      <circle cx="12" cy="20" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

