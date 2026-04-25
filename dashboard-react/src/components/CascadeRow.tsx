import {
  Calendar, CreditCard, Globe, Hash, Hotel, Link2, MessageSquare,
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

interface Props {
  row: Row;
  isFirst: boolean;
}

export function CascadeRow({ row, isFirst }: Props) {
  const Icon = ICON_MAP[row.tool] || Hash;
  return (
    <div
      className={cn(
        "relative grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3.5 px-4 py-3 transition-colors animate-in fade-in slide-in-from-bottom-1 duration-default",
        !isFirst && "border-t border-border",
        row.state === "running" && "bg-card",
        "hover:bg-paper-850/60"
      )}
    >
      {row.state === "running" && (
        <span className="absolute left-0 top-0 bottom-0 w-px bg-status-scheduled" />
      )}
      <span className="w-7 h-7 rounded border border-border bg-secondary grid place-items-center">
        <Icon className="w-3.5 h-3.5 text-paper-400" />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className={cn("inline-block w-2 h-2 rounded-full shrink-0", dotByState[row.state])} />
          <span className="text-body text-foreground truncate">{row.title}</span>
        </div>
        {row.body && (
          <div className="text-meta text-muted-foreground truncate mt-0.5">{row.body}</div>
        )}
        {row.ids && (
          <div className="text-meta tabular text-muted-foreground truncate mt-0.5 font-mono">{row.ids}</div>
        )}
      </div>
      {row.amount && (
        <div
          className={cn(
            "text-meta tabular shrink-0",
            amountColorByKind[row.amountKind || "default"]
          )}
        >
          {row.amount}
        </div>
      )}
    </div>
  );
}
