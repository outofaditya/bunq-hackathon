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
  // The Council (Money has feelings)
  | "personas_loaded"
  | "persona_speaks"
  | "persona_payout"
  | "persona_payout_error"
  | "persona_account_created"
  | "persona_funded"
  | "persona_voice_error"
  | "persona_cleanup"
  | "council_verdict"
  // Genesis (auto bring-up before user interaction)
  | "genesis_started"
  | "genesis_step_started"
  | "genesis_step_finished"
  | "genesis_complete"
  | "genesis_warning"
  | "genesis_error"
  // Confirmation prompt at end of council
  | "awaiting_confirmation"
  | "user_confirmation_received"
  | "user_confirmation"
  | "user_confirmation_timeout"
  // Trip mission (interactive chat + option cards + cartoon images)
  | "user_message"
  | "agent_message"
  | "options"
  | "option_image"
  | "confirmation_request"
  | "search_results"
  | "trip_phase"
  | string;

export interface Persona {
  account_id: number;
  iban: string;
  name: string;
  archetype: string;
  voice_id: string;
  blurb: string;
  catchphrase: string;
  balance_eur: number;
  is_demo: boolean;
}

export interface PersonaLine {
  persona_id: number;
  name: string;
  archetype: string;
  voice_id: string;
  stance: "for" | "against" | "neutral";
  text: string;
  audio_url?: string | null;
}

export interface CouncilVerdict {
  verdict: "APPROVE" | "REJECT" | "COMPROMISE";
  amount_eur: number;
  reasoning: string;
}

export interface GenesisStep {
  label: string;
  emoji: string;
  archetype: string;
  seed_eur: number;
  done: boolean;
}

export interface AwaitingConfirmation {
  question: string;
  action_summary: string;
  winning_persona_id: number | null;
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

export interface HealthInfo {
  ok: boolean;
  user_id: number | null;
  primary_id: number | null;
  public_url: string | null;
  mission_running: boolean;
}
