export type EventType =
  | "user_message"
  | "agent_message"
  | "options"
  | "option_image"
  | "confirmation_request"
  | "search_results"
  | "trip_phase"
  | "step_started"
  | "step_finished"
  | "step_error"
  | "narrate"
  | "narrate_audio"
  | "narrate_audio_error"
  | "browser_started"
  | "browser_screenshot"
  | "browser_action"
  | "browser_complete"
  | "balance_snapshot"
  | "awaiting_draft_approval"
  | "draft_resolved"
  | "draft_final"
  | "bunq_webhook"
  | "mission_complete"
  | "mission_error"
  | string;

export interface BusEvent {
  type: EventType;
  t: number;
  [key: string]: unknown;
}

export interface HealthInfo {
  ok: boolean;
  user_id: number | null;
  primary_id: number | null;
  public_url: string | null;
}

// =================== Trip mission ===================

export interface PackageSource {
  label: string;
  url: string;
}

export interface PackageOption {
  id: string;
  hotel: string;
  restaurant: string;
  extra: string;
  total_eur: number;
  notes: string;
  sources?: PackageSource[];
  image_url?: string | null;
  image_status?: "loading" | "ok" | "failed";
}

export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

export interface SearchGroup {
  query: string;
  results: SearchResult[];
}

export type TripPhase =
  | "UNDERSTANDING"
  | "AWAITING_CONFIRMATION"
  | "EXECUTING"
  | "DONE";

export type TripChatEntry =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string }
  | { kind: "options"; intro: string; options: PackageOption[]; selected?: string }
  | { kind: "confirmation"; summary: string; answered?: boolean };
