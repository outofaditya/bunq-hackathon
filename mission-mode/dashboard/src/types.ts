export type Phase = "UNDERSTANDING" | "AWAITING_CONFIRMATION" | "EXECUTING" | "DONE";

export type PackageSource = {
  label: string;
  url: string;
};

export type PackageOption = {
  id: string;
  hotel: string;
  restaurant: string;
  extra: string;
  total_eur: number;
  notes: string;
  sources?: PackageSource[];
};

export type ChatEntry =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string; streaming: boolean }
  | { kind: "tool"; name: string; status: "firing" | "ok" | "failed"; result?: unknown; input?: unknown; error?: string }
  | { kind: "options"; intro: string; options: PackageOption[]; selected?: string }
  | { kind: "confirmation"; summary: string; answered?: boolean }
  | { kind: "narration"; text: string };

export type TileName =
  | "create_sub_account"
  | "fund_sub_account"
  | "pay_vendor"
  | "create_draft_payment"
  | "schedule_recurring"
  | "request_from_partner"
  | "send_slack";

export type TileState = {
  name: TileName;
  label: string;
  status: "idle" | "firing" | "pending" | "ok" | "failed";
  detail?: string;
};

export type ServerEvent =
  | { type: "user_message"; text: string }
  | { type: "agent_text_delta"; text: string }
  | { type: "agent_message"; text: string }
  | { type: "agent_error"; error: string }
  | { type: "tool_call"; name: string; status: "firing" | "ok" | "failed"; input?: any; result?: any; error?: string }
  | { type: "options"; intro: string; options: PackageOption[] }
  | { type: "confirmation_request"; summary: string }
  | { type: "phase"; value: Phase }
  | { type: "narration"; text: string }
  | { type: "bunq_webhook"; category?: string; bunq_event?: string }
  | { type: "payment_event"; account_id?: number; amount_eur: number; description: string; sub_type?: string; category: string }
  | { type: "draft_payment_event"; draft_id: number; status: string }
  | { type: "schedule_event"; raw: any }
  | { type: "request_event"; status: string; amount_eur: number }
  | { type: "balance"; account_id: number; value_eur: number }
  | { type: "browser_frame"; jpeg_b64: string }
  | { type: "browser_status"; status: string; step?: string; hotel?: string; booking_ref?: string; query?: string; result_count?: number }
  | { type: "search_results"; query: string; results: { title: string; url: string; snippet: string }[] };
