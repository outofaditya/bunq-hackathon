export type EventType =
  | "mission_started"
  | "mission_complete"
  | "mission_error"
  | "mission_finalized"
  | "mission_routed"
  | "balance_snapshot"
  | "step_started"
  | "step_finished"
  | "step_error"
  | "narrate"
  | "narrate_audio"
  | "narrate_audio_error"
  | "voice_capture_started"
  | "voice_capture_error"
  | "transcript_ready"
  | "browser_started"
  | "browser_screenshot"
  | "browser_action"
  | "browser_complete"
  | "browser_idle"
  | "awaiting_draft_approval"
  | "draft_resolved"
  | "draft_final"
  | "bunq_webhook"
  // Sustainability donation prompt (post-mission)
  | "awaiting_donation"
  | "donation_decision_received"
  | "donation_decision"
  | "donation_decision_timeout"
  // Tax invoice scanner
  | "tax_scan_started"
  | "tax_extracted"
  | "tax_scan_error"
  | "awaiting_tax_confirm"
  | "tax_confirm_received"
  | "tax_payment_complete"
  | "tax_payment_skipped"
  | "tax_payment_error"
  | string;

export interface TaxExtraction {
  iban: string | null;
  bic: string | null;
  recipient: string | null;
  amount_eur: number | null;
  description: string | null;
}

export interface DonationPrompt {
  prompt_line: string;
  amount_eur: number;
  total_spent_eur: number;
  cause: string;
  totalSeconds: number;
  startedAt: number;
}

export interface BusEvent {
  type: EventType;
  t: number;
  [key: string]: unknown;
}

export interface CascadeRow {
  id: string;
  tool: string;
  title: string;
  body?: string;
  amount?: string;
  amountKind?: "neg" | "pending" | "default";
  state: "running" | "complete" | "pending" | "error";
  ids?: string;
}

export interface HealthInfo {
  ok: boolean;
  user_id: number | null;
  primary_id: number | null;
  public_url: string | null;
  mission_running: boolean;
}
