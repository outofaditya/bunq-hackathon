import {
  Calendar, CreditCard, Hash, Hotel, Link2, MessageSquare,
  Receipt, RefreshCcw, Snowflake, Tv, UtensilsCrossed,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { CascadeRow as Row } from "@/lib/types";

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  pay_vendor: Receipt,
  create_draft_payment: CreditCard,
  schedule_recurring_payment: RefreshCcw,
  request_money: MessageSquare,
  create_bunqme_link: Link2,
  set_card_status: Snowflake,
  freeze_home_card: Snowflake,
  unfreeze_home_card: CreditCard,
  book_restaurant: UtensilsCrossed,
  book_hotel: Hotel,
  subscribe_to_service: Tv,
  send_slack_message: MessageSquare,
  create_calendar_event: Calendar,
};

const dotByState: Record<Row["state"], string> = {
  running:  "bg-status-scheduled",
  pending:  "bg-status-upcoming",
  complete: "bg-status-complete",
  error:    "bg-status-overdue",
};

const amountColorByKind: Record<NonNullable<Row["amountKind"]>, string> = {
  neg:     "text-status-priority",
  pending: "text-status-upcoming",
  default: "text-foreground",
};

export function CascadeRow({ row, isFirst, index }: { row: Row; isFirst: boolean; index: number }) {
  const Icon = ICON_MAP[row.tool] || Hash;
  return (
    <div
      className={cn(
        "relative grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3.5 px-4 py-3 transition-all duration-default",
        !isFirst && "border-t border-border/70",
        row.state === "running" && "bg-card/80",
        "animate-in fade-in slide-in-from-bottom-1 duration-default ease-[cubic-bezier(0.16,1,0.3,1)]",
      )}
      style={{ animationDelay: `${Math.min(index * 24, 240)}ms`, animationFillMode: "both" }}
    >
      {row.state === "running" && (
        <span className="absolute left-0 top-0 bottom-0 w-px bg-status-scheduled" />
      )}
      <span
        className={cn(
          "w-7 h-7 rounded border bg-secondary grid place-items-center transition-colors duration-default",
          row.state === "running" && "border-status-scheduled/35 bg-status-scheduled/10",
          row.state === "complete" && "border-status-complete/30 bg-status-complete/[0.07]",
          row.state === "pending" && "border-status-upcoming/30 bg-status-upcoming/[0.07]",
          row.state === "error" && "border-status-overdue/35 bg-status-overdue/[0.08]",
          !["running","complete","pending","error"].includes(row.state) && "border-border",
        )}
      >
        <Icon
          className={cn(
            "w-3.5 h-3.5",
            row.state === "running" ? "text-status-scheduled" :
            row.state === "complete" ? "text-status-complete" :
            row.state === "pending" ? "text-status-upcoming" :
            row.state === "error" ? "text-status-overdue" :
            "text-paper-400",
          )}
        />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-block w-2 h-2 rounded-full shrink-0 transition-colors",
              dotByState[row.state],
              row.state === "running" && "animate-pulse",
            )}
          />
          <span className="text-body text-foreground truncate">{row.title}</span>
        </div>
        {row.body && (
          <div className="text-meta text-muted-foreground truncate mt-0.5">{row.body}</div>
        )}
        {row.ids && (
          <div className="text-meta tabular text-muted-foreground/80 truncate mt-0.5 font-mono">{row.ids}</div>
        )}
      </div>
      {row.amount && (
        <div
          className={cn(
            "text-body tabular shrink-0 font-medium",
            amountColorByKind[row.amountKind || "default"],
          )}
        >
          {row.amount}
        </div>
      )}
    </div>
  );
}
