import { useEffect, useRef, useState } from "react";
import type { ChatEntry, Phase, PackageOption } from "./types";
import MarkdownText from "./markdown";

type Props = {
  entries: ChatEntry[];
  phase: Phase;
  onSend: (text: string) => void;
  onSelectOption: (entryIdx: number, optionId: string) => void;
  onConfirm: (entryIdx: number, answer: "yes" | "no") => void;
};

export default function Chat({ entries, phase, onSend, onSelectOption, onConfirm }: Props) {
  const [draft, setDraft] = useState("");
  const [recording, setRecording] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = draft.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setDraft("");
  }

  async function toggleMic() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      setRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const fd = new FormData();
        fd.append("audio", blob, "recording.webm");
        try {
          const resp = await fetch("/stt", { method: "POST", body: fd });
          const j = await resp.json();
          if (j.transcript) onSend(j.transcript);
        } catch {
          // ignore
        }
      };
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (e) {
      console.error("mic error", e);
    }
  }

  return (
    <section className="chat">
      <div className="chat-scroll" ref={scrollRef}>
        {entries.map((entry, idx) => (
          <ChatBubble
            key={idx}
            entry={entry}
            index={idx}
            onSelectOption={onSelectOption}
            onConfirm={onConfirm}
          />
        ))}
      </div>
      <form className="composer" onSubmit={handleSubmit}>
        <button
          type="button"
          className={`mic ${recording ? "recording" : ""}`}
          onClick={toggleMic}
          title={recording ? "Stop recording" : "Hold mic to talk"}
        >
          {recording ? "■" : "🎙"}
        </button>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            phase === "EXECUTING"
              ? "Agent is executing…"
              : phase === "AWAITING_CONFIRMATION"
              ? "Say yes to confirm, or type a change…"
              : "Describe your trip…"
          }
        />
        <button type="submit" className="send">
          Send
        </button>
      </form>
    </section>
  );
}

function ChatBubble({
  entry,
  index,
  onSelectOption,
  onConfirm,
}: {
  entry: ChatEntry;
  index: number;
  onSelectOption: (i: number, id: string) => void;
  onConfirm: (i: number, a: "yes" | "no") => void;
}) {
  if (entry.kind === "user") {
    return (
      <div className="bubble user">
        <MarkdownText text={entry.text} variant="user" />
      </div>
    );
  }
  if (entry.kind === "agent") {
    return (
      <div className={`bubble agent${entry.streaming ? " streaming" : ""}`}>
        <MarkdownText text={entry.text} variant="agent" streaming={entry.streaming} />
      </div>
    );
  }
  if (entry.kind === "tool") {
    return (
      <div className={`tool-chip status-${entry.status}`}>
        <span className="tool-dot" />
        <span className="tool-name">{formatToolName(entry.name)}</span>
        <span className="tool-status">{entry.status}</span>
        {entry.error && <span className="tool-error">{entry.error.slice(0, 80)}</span>}
      </div>
    );
  }
  if (entry.kind === "options") {
    return (
      <div className="options-rack">
        <div className="options-intro">{entry.intro}</div>
        <div className="options-grid">
          {entry.options.map((opt) => (
            <OptionCard
              key={opt.id}
              option={opt}
              selected={entry.selected === opt.id}
              disabled={!!entry.selected}
              onSelect={() => onSelectOption(index, opt.id)}
            />
          ))}
        </div>
      </div>
    );
  }
  if (entry.kind === "confirmation") {
    return (
      <div className={`confirmation ${entry.answered ? "answered" : ""}`}>
        <div className="confirmation-summary">{entry.summary}</div>
        {!entry.answered && (
          <div className="confirmation-actions">
            <button className="btn-primary" onClick={() => onConfirm(index, "yes")}>
              Yes, go
            </button>
            <button className="btn-ghost" onClick={() => onConfirm(index, "no")}>
              Cancel
            </button>
          </div>
        )}
      </div>
    );
  }
  if (entry.kind === "narration") {
    return (
      <div className="narration">
        <span className="narration-icon">🔊</span>
        <span>{entry.text}</span>
      </div>
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
  const imgStatus = option.image_status ?? (option.image_url ? "ok" : undefined);
  return (
    <div className={`option-card ${selected ? "selected" : ""} ${disabled && !selected ? "disabled" : ""}`}>
      <button
        type="button"
        className="option-card-button"
        disabled={disabled && !selected}
        onClick={onSelect}
      >
        <div className="option-image">
          {option.image_url ? (
            <img src={option.image_url} alt={option.hotel} loading="lazy" />
          ) : imgStatus === "failed" ? (
            <div className="option-image-fallback">🌍</div>
          ) : (
            <div className="option-image-skeleton">
              <span className="option-image-shimmer" />
              <span className="option-image-label">painting…</span>
            </div>
          )}
        </div>
        <div className="option-id">{option.id.toUpperCase()}</div>
        <div className="option-hotel">{option.hotel}</div>
        <div className="option-line">🍽 {option.restaurant}</div>
        <div className="option-line">✨ {option.extra}</div>
        <div className="option-notes">{option.notes}</div>
        <div className="option-price">€{option.total_eur.toFixed(0)}</div>
      </button>
      {hasSources && (
        <div className="option-sources" onClick={(e) => e.stopPropagation()}>
          <div className="option-sources-label">Sources</div>
          <div className="option-sources-list">
            {option.sources!.slice(0, 5).map((s, i) => (
              <a
                key={i}
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="option-source"
                title={s.url}
              >
                <span className="option-source-dot">↗</span>
                {s.label || hostnameOf(s.url)}
              </a>
            ))}
          </div>
        </div>
      )}
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

function formatToolName(name: string): string {
  return name.replace(/_/g, " ");
}
