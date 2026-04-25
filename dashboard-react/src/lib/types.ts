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
  | string;

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
