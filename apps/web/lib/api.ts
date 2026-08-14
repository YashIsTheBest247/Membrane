/**
 * Client for the Membrane proxy.
 *
 * Everything the dashboard renders comes from the proxy's own audit trail and
 * event stream. The dashboard holds no state of its own and stores nothing —
 * it is a window onto the API, which is the only place decisions are recorded.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_MEMBRANE_API ?? "http://localhost:8080";

export type Verdict = "pass" | "strip" | "hold" | "block";

export interface FeedEvent {
  seq: number;
  kind: string;
  ts: number;
  session_id?: string;
  layer?: string;
  verdict?: Verdict;
  source?: string;
  reason?: string;
  span?: string;
  family?: string;
  score?: number;
  tier?: string;
  preview?: string | null;
  tool?: string;
  capability?: string;
  action_id?: string;
  card?: DecisionCard;
  spans?: number;
  quarantined?: number;
  removed?: number;
  trust_score?: number;
  latency_ms?: number;
  status?: string;
  server?: string;
  poisoned_tools?: string[];
}

export interface DecisionCard {
  what: {
    tool: string;
    capability: string;
    sensitivity: string;
    arguments: { name: string; shape: string; tainted: boolean }[];
  };
  why: string;
  provenance: {
    argument: string;
    chain: string;
    lineage: { span: string; provenance: string; source: string; match: string }[];
  }[];
  diff: {
    authorised_capabilities: string[];
    attempted_capability: string;
    in_envelope: boolean;
    contract_id: string | null;
    contract_ttl_remaining_seconds: number;
  };
  egress: { kind: string; detail: string; arg: string }[];
}

export interface HeldAction {
  action_id: string;
  session_id: string;
  tool: string;
  capability: string;
  sensitivity: string;
  status: "pending" | "approved" | "denied" | "expired";
  reason: string;
  card: DecisionCard;
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  telegram_delivered: boolean;
}

export interface Stats {
  window_hours: number;
  by_verdict: Record<string, number>;
  by_layer: Record<string, number>;
  events: number;
  sessions: number;
  corpus_entries: number;
  latency_ms: { p50: number; p95: number; p99: number; samples: number };
  escalation: { available: boolean; calls: number; cache_hits: number; failures: number };
  taint_sessions_in_memory: number;
  breaker_open_sessions: string[];
  privacy: { content_retained: boolean; live_preview_enabled: boolean };
}

export interface SessionRow {
  session_id: string;
  subject: string;
  created_at: string;
  last_seen_at: string;
  spans_seen: number;
  holds: number;
  blocks: number;
  breaker_open: boolean;
}

export interface AuditEventRow {
  seq: number;
  at: string;
  layer: string;
  verdict: Verdict;
  reason: string;
  span: string | null;
  source: string | null;
  provenance: string | null;
  signals: Record<string, unknown>;
  latency_ms: number;
  entry_hash: string;
  session_id?: string;
}

export interface Replay {
  session_id: string;
  integrity: { ok: boolean; events: number; first_broken_seq: number | null };
  events: AuditEventRow[];
  provenance_graph: {
    nodes: { span: string; provenance: string; source: string }[];
    edges: { from: string; to: string }[];
  };
  held_actions: {
    action_id: string;
    tool: string;
    capability: string;
    status: string;
    reason: string;
    created_at: string;
    resolved_by: string | null;
    card: DecisionCard;
  }[];
}

export interface BenchRun {
  run_id: string;
  label: string;
  created_at: string;
  total_cases: number;
  unprotected_success: number;
  protected_success: number;
  asr_reduction: number;
  false_positive_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  meets_targets: Record<string, boolean>;
  detail: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new ApiError(`${init?.method ?? "GET"} ${path} → ${response.status}`,
      response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  stats: () => request<Stats>("/v1/stats"),
  sessions: () => request<{ sessions: SessionRow[] }>("/v1/sessions?limit=100"),
  replay: (id: string) => request<Replay>(`/v1/sessions/${id}/replay`),
  evidence: (id: string) => request<unknown>(`/v1/sessions/${id}/evidence`),
  recentEvents: () =>
    request<{ events: AuditEventRow[] }>("/v1/events/recent?limit=100"),
  recentFeed: () => request<{ events: FeedEvent[] }>("/v1/stream/recent?limit=100"),
  pending: () =>
    request<{ pending: HeldAction[]; telegram_configured: boolean; timeout_seconds: number }>(
      "/v1/approvals"),
  recentApprovals: () =>
    request<{ actions: HeldAction[] }>("/v1/approvals/recent?limit=40"),
  decide: (id: string, decision: "approve" | "deny") =>
    request<HeldAction>(`/v1/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, resolved_by: "dashboard" }),
    }),
  trust: () =>
    request<{ sources: { source: string; score: number; incidents: number; clean_spans: number; last_incident_at: string | null }[] }>(
      "/v1/trust"),
  corpus: () =>
    request<{ entries: { span: string; family: string; hits: number; first_seen_at: string }[]; by_family: Record<string, number> }>(
      "/v1/corpus"),
  leaderboard: () =>
    request<{ targets: Record<string, number>; entries: (BenchRun & { rank: number })[] }>(
      "/v1/bench/leaderboard"),
  capabilities: () =>
    request<{ capabilities: { capability: string; sensitivity: string; description: string }[] }>(
      "/v1/capabilities"),
  simulate: (content: string, contentType: string) =>
    request<SimulationResult>("/v1/simulate", {
      method: "POST",
      body: JSON.stringify({
        session_id: `playground_${Date.now()}`,
        content,
        content_type: contentType,
        source: "playground",
      }),
    }),
  scanMcp: (server: string, document: string) =>
    request<McpReport>("/v1/mcp/scan-file", {
      method: "POST",
      body: JSON.stringify({ server, document }),
    }),
};

export interface SimulationResult {
  verdict: Verdict;
  forwarded_to_agent: string;
  l1: {
    summary: { removals: Record<string, number>; stats: Record<string, number>; folded_words: number };
    removed: { kind: string; detail: string; depth: number; recovered: string; span: string }[];
  };
  l2: {
    summary: { spans: number; quarantined: number; escalations: number; max_score: number; families: string[] };
    spans: {
      index: number;
      channel: string;
      tier: string;
      reason: string;
      score: number;
      family: string;
      matched: string[];
      text: string;
      span: string;
      targeting?: number;
    }[];
  };
}

export interface McpReport {
  server: string;
  verdict: Verdict;
  reason: string;
  tools_scanned: number;
  poisoned_tools: string[];
  tools: {
    tool: string;
    verdict: Verdict;
    max_score: number;
    findings: { path: string; score: number; family: string; reason: string; hidden_payload: boolean }[];
  }[];
}

/** Subscribe to the live verdict feed. Returns an unsubscribe function. */
export function subscribe(
  onEvent: (event: FeedEvent) => void,
  onStatus?: (connected: boolean) => void,
): () => void {
  const source = new EventSource(`${API_BASE}/v1/stream?backlog=25`);
  const kinds = [
    "ingest", "attack.blocked", "action.held", "action.blocked", "action.passed",
    "approval.resolved", "approval.expired", "breaker.tripped", "mcp.scanned",
    "bench.completed",
  ];
  const handler = (message: MessageEvent) => {
    try {
      onEvent(JSON.parse(message.data) as FeedEvent);
    } catch {
      /* a malformed frame is not worth tearing the stream down for */
    }
  };
  kinds.forEach((kind) => source.addEventListener(kind, handler as EventListener));
  source.onopen = () => onStatus?.(true);
  source.onerror = () => onStatus?.(false);
  return () => {
    kinds.forEach((kind) => source.removeEventListener(kind, handler as EventListener));
    source.close();
  };
}
