/**
 * Typed NWIS API client.
 *
 * Every value rendered anywhere in this application arrives through this module.
 * There are no fixture objects, no seeded arrays and no default values standing in for
 * data — if the backend cannot supply something, the UI shows an explicit state.
 */

// In production the SPA is served from the same origin as the API through the nginx
// proxy, so both bases are empty and every request is relative. In development they
// point at the local backend on its own port.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const WS_BASE = import.meta.env.VITE_WS_BASE_URL ?? "ws://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(message: string, status: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = (await response.json())?.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      detail ?? `Request failed (${response.status})`,
      response.status,
      detail,
    );
  }
  return (await response.json()) as T;
}

// ------------------------------------------------------------------ domain types

export interface Well {
  id: number;
  name: string;
  reference: string | null;
  field_name: string | null;
  operator: string | null;
  latitude: number | null;
  longitude: number | null;
  location_source: string | null;
  total_depth_md_m: number | null;
  water_depth_m: number | null;
  has_logs: boolean;
  has_telemetry: boolean;
  has_trajectory: boolean;
  is_active: boolean;
  status: string | null;
  source_dataset: string | null;
}

export interface PaginatedWells {
  items: Well[];
  total: number;
  limit: number;
  offset: number;
}

export interface NearbyWell {
  well: Well;
  distance_km: number;
}

export interface AnalogueComponent {
  name: string;
  value: number;
  weight: number;
  detail: string;
}

export interface AnalogueMatch {
  well: Well;
  score: number;
  segment_top_m: number | null;
  segment_base_m: number | null;
  distance_km: number | null;
  dominant_formation: string | null;
  dominant_group: string | null;
  components: AnalogueComponent[];
  dimensions_used: string[];
  dimensions_unavailable: string[];
}

export interface AnalogueResponse {
  query_well: Well;
  query_depth_m: number | null;
  top_k: number;
  weights: Record<string, number>;
  matches: AnalogueMatch[];
  diagnostics: Record<string, unknown>;
}

export interface FormationInterval {
  group_name: string | null;
  formation_name: string | null;
  depth_top_m: number;
  depth_base_m: number;
  sample_count: number | null;
  source_dataset: string | null;
}

export interface TrajectoryStation {
  md_m: number;
  tvd_m: number | null;
  inclination_deg: number | null;
  azimuth_deg: number | null;
  north_offset_m: number | null;
  east_offset_m: number | null;
  dogleg_severity_deg_per_m: number | null;
  station_type: string | null;
}

export interface DrillingEvent {
  id: number;
  well_id: number;
  occurred_at: string | null;
  event_type: string;
  category: string | null;
  severity: string | null;
  depth_start_m: number | null;
  depth_end_m: number | null;
  depth_source: string | null;
  formation_name: string | null;
  description: string;
  extraction_method: string | null;
  source_dataset: string | null;
  source_reference: string | null;
}

export interface Mitigation {
  id: number;
  event_id: number;
  action_taken: string;
  outcome: string | null;
  outcome_status: string | null;
  source_dataset: string | null;
}

export interface TelemetrySample {
  recorded_at: string;
  bit_depth_m: number | null;
  hole_depth_m: number | null;
  channels: Record<string, number | null>;
  circulating: boolean | null;
  rotating: boolean | null;
  tripping: boolean | null;
  operations_active: boolean | null;
}

export interface RiskComponent {
  name: string;
  value: number;
  weight: number;
  explanation: string;
  available: boolean;
}

export interface HistoricalEvidence {
  well_name: string;
  similarity_score: number;
  event_id: number;
  event_type: string;
  depth_start_m: number | null;
  depth_end_m: number | null;
  distance_from_bit_m: number | null;
  description: string;
  formation_name: string | null;
  depth_source: string | null;
  source_dataset: string | null;
  source_reference: string | null;
  mitigations: Array<Record<string, unknown>>;
}

export interface RiskAssessment {
  well_id: number;
  well_name: string;
  depth_m: number | null;
  mode: string;
  score: number;
  risk_level: string;
  /** Null under hybrid_indicator mode by design — the UI must show that, not a zero. */
  probability: number | null;
  components: RiskComponent[];
  historical_evidence: HistoricalEvidence[];
  analogue_wells: Array<Record<string, unknown>>;
  contributing_features: Array<Record<string, unknown>>;
  narrative: string;
  notes: string[];
}

export interface RiskAlert {
  id: number;
  well_id: number;
  risk_assessment_id: number | null;
  raised_at: string;
  depth_m: number | null;
  level: string;
  title: string;
  summary: string;
  status: string;
  occurrence_count: number;
}

export interface EngineerAction {
  id: number;
  alert_id: number;
  well_id: number;
  decision: string;
  action_description: string | null;
  outcome: string | null;
  engineer_name: string | null;
  recorded_at: string;
  available_for_training: boolean;
}

export interface ModelVersion {
  id: number;
  name: string;
  version: string;
  task: string;
  algorithm: string;
  dataset: string | null;
  dataset_rows: number | null;
  trained_at: string | null;
  training_seconds: number | null;
  is_active: boolean;
  feature_columns: string[];
  hyperparameters: Record<string, unknown>;
  metrics: Record<string, any>;
  split_summary: Record<string, unknown>;
  limitations: string[];
}

export interface LithologyPrediction {
  depth_md_m: number;
  lithology_code: number;
  lithology_name: string;
  probability: number | null;
}

export interface ReplayState {
  well_id: number | null;
  well_name: string | null;
  status: string;
  speed: number;
  current_index: number;
  total_samples: number;
  current_timestamp: string | null;
  current_depth_m: number | null;
  source: string;
}

export interface PassageMatch {
  chunk_id: number;
  document_id: number;
  document_title: string;
  well_id: number | null;
  well_name: string | null;
  page_number: number | null;
  /** Cosine similarity in [-1, 1]; higher is closer. */
  similarity: number;
  text: string;
}

/**
 * Search results together with what was actually searched. `provenance` reports how much
 * of the corpus is indexed, so a partial index is visible rather than being presented as
 * a complete search.
 */
export interface DocumentSearchResponse {
  results: PassageMatch[];
  provenance: {
    query: string;
    passages_searched: number;
    passages_indexed: number;
    passages_total: number;
    model: string;
    similarity_metric: string;
    min_similarity: number;
    restricted_to_well_id: number | null;
    note?: string;
  };
}

export interface SystemStatus {
  application: string;
  environment: string;
  database: Record<string, any>;
  counts: Record<string, number>;
  models: Array<Record<string, any>>;
  warnings: string[];
}

// ---------------------------------------------------------------------- endpoints

export const api = {
  status: () => request<SystemStatus>("/api/status"),

  listWells: (params: {
    limit?: number;
    offset?: number;
    search?: string;
    dataset?: string;
    hasTelemetry?: boolean;
  } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.offset) query.set("offset", String(params.offset));
    if (params.search) query.set("search", params.search);
    if (params.dataset) query.set("dataset", params.dataset);
    if (params.hasTelemetry !== undefined)
      query.set("has_telemetry", String(params.hasTelemetry));
    return request<PaginatedWells>(`/api/wells?${query}`);
  },

  getWell: (id: number) => request<Well>(`/api/wells/${id}`),

  nearby: (id: number, radiusKm?: number, limit = 25) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (radiusKm) query.set("radius_km", String(radiusKm));
    return request<NearbyWell[]>(`/api/wells/${id}/nearby?${query}`);
  },

  analogues: (id: number, depthM?: number | null, topK?: number) => {
    const query = new URLSearchParams();
    if (depthM != null) query.set("depth_m", String(depthM));
    if (topK) query.set("top_k", String(topK));
    return request<AnalogueResponse>(`/api/wells/${id}/analogues?${query}`);
  },

  formations: (id: number) =>
    request<FormationInterval[]>(`/api/wells/${id}/formations`),

  trajectory: (id: number) =>
    request<TrajectoryStation[]>(`/api/wells/${id}/trajectory`),

  lithology: (id: number, limit = 4000) =>
    request<LithologyPrediction[]>(`/api/wells/${id}/lithology?limit=${limit}`),

  events: (id: number, limit = 200) =>
    request<DrillingEvent[]>(`/api/wells/${id}/events?limit=${limit}`),

  mitigations: (eventId: number) =>
    request<Mitigation[]>(`/api/events/${eventId}/mitigations`),

  telemetry: (id: number, limit = 1000, offset = 0, activeOnly = false) =>
    request<TelemetrySample[]>(
      `/api/wells/${id}/telemetry?limit=${limit}&offset=${offset}&active_only=${activeOnly}`,
    ),

  risk: (id: number, depthM?: number | null, anomalyScore?: number | null) => {
    const query = new URLSearchParams();
    if (depthM != null) query.set("depth_m", String(depthM));
    if (anomalyScore != null) query.set("anomaly_score", String(anomalyScore));
    return request<RiskAssessment>(`/api/wells/${id}/risk?${query}`);
  },

  wellAlerts: (id: number) => request<RiskAlert[]>(`/api/wells/${id}/alerts`),
  allAlerts: (limit = 100) => request<RiskAlert[]>(`/api/alerts?limit=${limit}`),
  alertExplanation: (alertId: number) =>
    request<RiskAssessment>(`/api/alerts/${alertId}/explanation`),

  recordAction: (payload: {
    alert_id: number;
    decision: string;
    action_description?: string;
    outcome?: string;
    engineer_name?: string;
  }) =>
    request<EngineerAction>("/api/engineer-actions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  wellActions: (id: number) =>
    request<EngineerAction[]>(`/api/wells/${id}/engineer-actions`),

  searchDocuments: (query: string, options?: { wellId?: number; limit?: number }) => {
    const params = new URLSearchParams({ q: query });
    if (options?.wellId != null) params.set("well_id", String(options.wellId));
    if (options?.limit != null) params.set("limit", String(options.limit));
    return request<DocumentSearchResponse>(`/api/documents/search?${params}`);
  },

  models: () => request<ModelVersion[]>("/api/models"),
  model: (name: string) => request<ModelVersion>(`/api/models/${name}`),

  replayState: (id: number) => request<ReplayState>(`/api/replay/${id}`),
  replayStart: (id: number, speed?: number) =>
    request<ReplayState>(
      `/api/replay/${id}/start${speed ? `?speed=${speed}` : ""}`,
      { method: "POST" },
    ),
  replayPause: (id: number) =>
    request<ReplayState>(`/api/replay/${id}/pause`, { method: "POST" }),
  replayResume: (id: number) =>
    request<ReplayState>(`/api/replay/${id}/resume`, { method: "POST" }),
  replayStop: (id: number) =>
    request<ReplayState>(`/api/replay/${id}/stop`, { method: "POST" }),
  replaySpeed: (id: number, speed: number) =>
    request<ReplayState>(`/api/replay/${id}/speed?speed=${speed}`, {
      method: "POST",
    }),
};

export function telemetrySocketUrl(wellId: number): string {
  const path = `/ws/telemetry/${wellId}`;
  if (WS_BASE) return `${WS_BASE}${path}`;
  // Same-origin deployment: a WebSocket needs an absolute URL, so derive the scheme and
  // host from the page. This also selects wss:// automatically when served over HTTPS.
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}${path}`;
}
