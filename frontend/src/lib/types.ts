export type Role = "operator" | "kierownik_zmiany" | "agent_service";

export interface Me {
  subject: string;
  role: Role;
  display_name: string;
  scopes: string[];
}

export interface Geometry {
  aisle_count: number;
  bays: number;
  levels: number;
  width: number;
  front_y: number;
  back_y: number;
  rack_start_y: number;
  aisle_spacing: number;
  depot: { x: number; y: number };
  aisles: { aisle: number; x: number; zone: string }[];
}

export interface Zone {
  zone: string;
  name: string;
  kind: string;
  aisle_from: number;
  aisle_to: number;
}

export interface LocationRow {
  location_id: string;
  zone: string;
  aisle: number;
  bay: number;
  level: number;
  x: number;
  y: number;
  chilled: boolean;
  adr: boolean;
}

export interface Layout {
  zones: Zone[];
  geometry: Geometry;
  locations: LocationRow[];
}

export interface WorkerPos {
  worker_id: string;
  x: number;
  y: number;
  state: "idle" | "walking" | "picking" | "break" | "packing";
  active: boolean;
}

export interface Route {
  wave_id: string;
  worker_id: string;
  algorithm: string;
  distance_m: number;
  baseline_distance_m: number;
  duration_s: number;
  trips: number;
  status: string;
  progress_stop: number;
  stops: { type: string; location_id?: string; x: number; y: number }[];
  path: [number, number][];
}

export interface Wave {
  wave_id: string;
  status: "validated" | "released" | "in_progress" | "completed" | "discarded";
  created_by: string;
  rationale: string;
  cutoff_at: string;
  created_at: string;
  released_at: string | null;
  completed_at: string | null;
  est_distance_m: number;
  est_duration_s: number;
  total_lines: number;
  picked_lines: number;
  progress: number;
  assignments: { worker_id: string; order_ids: string[] }[];
}

export interface Alert {
  kind: string;
  severity: "low" | "medium" | "high";
  message: string;
}

export interface Kpis {
  sim_now: string;
  window: string;
  lines_picked: number;
  lines_per_hour: number;
  lines_per_hour_per_worker: number;
  distance_per_order_m: number | null;
  otif: number | null;
  shipped: number;
  utilization: number;
  workers_on_shift: number;
  workers_busy: number;
  pack_queue: number;
  open_orders: number;
  orders_at_risk: number;
  orders_overdue: number;
  waves_by_status: Record<string, number>;
  stockouts: { event_ref: string; ts: string; location_id: string; sku_id: string; need: number; found: number }[];
  picks_by_aisle: Record<string, number>;
  congestion_by_aisle: Record<string, number>;
  alerts: Alert[];
}

export interface TimePoint {
  t: string;
  lines_per_hour: number;
  stockouts: number;
  shipped: number;
  on_time: number;
}

export interface SimState {
  sim_now: string;
  running: boolean;
  speed: number;
  scenario: string;
  seed: number;
  size: string;
  pack_queue: number;
}

export interface ApprovalRequest {
  request_id: string;
  kind: "order_cancellation" | "inventory_adjustment";
  status: string;
  target_type: string;
  target_id: string;
  reason_code: string;
  flags: string[];
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  failure_reason: string | null;
  version: number;
  requested_by: string;
  requested_via: string;
  justification: string;
  payload: Record<string, unknown>;
  before_state: Record<string, unknown>;
  after_state: Record<string, unknown>;
  source_context: {
    customer_note?: string;
    customer_ref?: string;
    note_signals?: string[];
    order_flags?: string[];
    sku_name?: string;
    evidence?: { ref: string; kind: string; details: Record<string, unknown> } | null;
  };
  decided_by: string | null;
  decision_comment: string | null;
  current_target?: Record<string, unknown> | null;
  target_changed?: boolean;
}

export interface ReslottingProposal {
  proposal_id: string;
  status: string;
  created_by: string;
  created_at: string;
  rationale: string;
  moves: { sku_id: string; from_location: string; to_location: string; qty: number; est_gain_m_per_day: number }[];
  est_gain_m_per_day: number;
  version: number;
}

export interface AuditEntry {
  seq: number;
  ts: string;
  source: string;
  actor: string;
  actor_type: string;
  action: string;
  target: string;
  args: Record<string, unknown>;
  decision: "allow" | "deny";
  outcome: string;
  reason: string;
  prev_hash: string;
  hash: string;
}

export interface AgentReport {
  id: number;
  created_at: string;
  sim_time: string;
  author: string;
  mode: string;
  summary: string;
  decisions: { action: string; target: string; why: string }[];
  metrics: Record<string, number>;
}
