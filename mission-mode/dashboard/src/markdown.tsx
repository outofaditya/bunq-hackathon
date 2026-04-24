import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ gfm: true, breaks: true });

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

const STREAM_CARET = "​__STREAM_CARET__​";

export function healStream(raw: string): string {
  let s = raw;

  const fenceCount = (s.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) s += "\n```";

  const segments = s.split(/(```[\s\S]*?```)/g);
  for (let i = 0; i < segments.length; i++) {
    if (i % 2 === 1) continue;
    segments[i] = healInline(segments[i]);
  }
  return segments.join("");
}

function healInline(seg: string): string {
  let out = seg;

  const linkOpen = /\[([^\]]*)$/;
  const m = out.match(linkOpen);
  if (m && !m[1].includes("\n")) {
    out = out.replace(linkOpen, "$1");
  }

  const partialLink = /\[([^\]]+)\]\(([^)]*)$/;
  const pm = out.match(partialLink);
  if (pm) {
    out = out.replace(partialLink, "[$1]($2)");
  }

  const ticks = (out.match(/`/g) || []).length;
  if (ticks % 2 === 1) out += "`";

  const dstars = (out.match(/\*\*/g) || []).length;
  if (dstars % 2 === 1) out += "**";

  const lone = out.replace(/\*\*/g, "").match(/\*/g);
  if (lone && lone.length % 2 === 1) out += "*";

  const dund = (out.match(/__/g) || []).length;
  if (dund % 2 === 1) out += "__";

  return out;
}

type Props = {
  text: string;
  streaming?: boolean;
  variant?: "agent" | "user";
};

export default function MarkdownText({ text, streaming, variant = "agent" }: Props) {
  const html = useMemo(() => {
    let source = streaming ? healStream(text) : text;
    if (streaming) {
      const trailingNL = source.endsWith("\n") ? "" : "";
      source = source + trailingNL + STREAM_CARET;
    }
    const raw = marked.parse(source, { async: false }) as string;
    const withCaret = raw.replace(
      STREAM_CARET,
      '<span class="md-caret">▍</span>',
    );
    return DOMPurify.sanitize(withCaret, {
      ADD_ATTR: ["target", "rel"],
    });
  }, [text, streaming]);

  return (
    <div
      className={`markdown md-${variant}${streaming ? " md-streaming" : ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
