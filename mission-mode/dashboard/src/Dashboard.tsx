import { useEffect, useState } from "react";
import type { TileState } from "./types";

type SearchGroup = { query: string; results: { title: string; url: string; snippet: string }[] };

type Props = {
  tiles: TileState[];
  balance: { goal: number; value: number; name: string } | null;
  narration: string;
  sessionId: string | null;
  browserFrame: string | null;
  browserStatus: { status: string; step?: string; hotel?: string; booking_ref?: string; query?: string } | null;
  searchFeed: SearchGroup[];
};

export default function Dashboard({ tiles, balance, narration, sessionId, browserFrame, browserStatus, searchFeed }: Props) {
  const [displayValue, setDisplayValue] = useState(0);

  useEffect(() => {
    if (!balance) return;
    const target = balance.value;
    const start = displayValue;
    const delta = target - start;
    if (Math.abs(delta) < 0.5) return;
    const duration = 800;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplayValue(start + delta * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [balance?.value]);

  const pct = balance ? Math.min(100, (displayValue / balance.goal) * 100) : 0;

  return (
    <section className="dashboard">
      <div className="dash-section balance-card">
        {balance ? (
          <>
            <div className="balance-label">Sub-account</div>
            <div className="balance-name">{balance.name}</div>
            <div className="balance-amount">
              €{displayValue.toFixed(2)}
              <span className="balance-goal"> / €{balance.goal.toFixed(0)}</span>
            </div>
            <div className="balance-bar">
              <div className="balance-fill" style={{ width: `${pct}%` }} />
            </div>
          </>
        ) : (
          <div className="balance-empty">No sub-account yet — waiting for mission to start.</div>
        )}
      </div>

      <div className="dash-section tiles">
        <div className="tiles-label">Actions</div>
        <div className="tile-strip">
          {tiles.map((tile) => (
            <div key={tile.name} className={`tile status-${tile.status}`}>
              <div className="tile-header">
                <span className="tile-dot" />
                <span className="tile-label">{tile.label}</span>
              </div>
              {tile.detail && <div className="tile-detail">{tile.detail}</div>}
              {tile.name === "create_draft_payment" && tile.status === "pending" && (
                <button
                  className="tile-action"
                  onClick={async () => {
                    try {
                      await fetch("/simulate-approve", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ session_id: sessionId }),
                      });
                    } catch {
                      // ignore
                    }
                  }}
                >
                  Simulate approve tap
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="dash-section browser-panel">
        <div className="browser-label">
          <span>Agent Browser</span>
          {browserStatus && (
            <span className="browser-badge">
              {browserStatus.status === "done" && browserStatus.booking_ref
                ? `booked · ${browserStatus.booking_ref}`
                : browserStatus.query
                ? `${browserStatus.status} · "${browserStatus.query.slice(0, 30)}${browserStatus.query.length > 30 ? "…" : ""}"`
                : browserStatus.step
                ? `${browserStatus.status} · ${browserStatus.step}`
                : browserStatus.status}
            </span>
          )}
        </div>
        <div className="browser-viewport">
          {browserFrame ? (
            <img
              className="browser-frame"
              src={`data:image/jpeg;base64,${browserFrame}`}
              alt="Agent browser view"
            />
          ) : (
            <div className="browser-placeholder">
              Playwright panel — activates when the agent searches or books.
            </div>
          )}
        </div>
      </div>

      {searchFeed.length > 0 && (
        <div className="dash-section research-feed">
          <div className="research-label">Research · sources found</div>
          <div className="research-groups">
            {searchFeed.map((group, gi) => (
              <div key={gi} className="research-group">
                <div className="research-query">
                  <span className="research-q-icon">🔍</span>
                  <span className="research-q-text">{group.query}</span>
                  <span className="research-q-count">{group.results.length}</span>
                </div>
                <ul className="research-results">
                  {group.results.slice(0, 5).map((r, ri) => {
                    let host = "";
                    try {
                      host = new URL(r.url).hostname.replace(/^www\./, "");
                    } catch {
                      host = "";
                    }
                    return (
                      <li key={ri} className="research-result">
                        <a href={r.url} target="_blank" rel="noopener noreferrer" className="research-result-link">
                          <div className="research-result-title">{r.title || r.url}</div>
                          <div className="research-result-host">{host}</div>
                          {r.snippet && <div className="research-result-snippet">{r.snippet}</div>}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}

      {narration && (
        <div className="narration-badge">
          <span className="narration-icon">🔊</span>
          <span>{narration}</span>
        </div>
      )}
    </section>
  );
}
