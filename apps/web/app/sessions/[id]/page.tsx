"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api, API_BASE, Replay } from "@/lib/api";
import { ErrorBanner, PageHead, Verdict } from "@/components/shell";

const LAYER_LABEL: Record<string, string> = {
  l1_sanitiser: "L1 sanitiser",
  l2_separator: "L2 separator",
  l3_taint: "L3 taint tracker",
  l4_capability: "L4 capability firewall",
  egress: "egress inspection",
  mcp_scan: "MCP scanner",
  breaker: "circuit breaker",
  approval: "human loop",
};

export default function SessionReplayPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = use(params);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [verifying, setVerifying] = useState("");
  const [verifyResult, setVerifyResult] = useState<string | null>(null);
  const [selectedSpan, setSelectedSpan] = useState<string | null>(null);

  useEffect(() => {
    api.replay(id).then(setReplay).catch(setError);
  }, [id]);

  const verify = async () => {
    if (!selectedSpan) return;
    const response = await fetch(`${API_BASE}/v1/verify-span`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: verifying, span_hash: selectedSpan }),
    });
    const body = await response.json();
    setVerifyResult(body.matches
      ? "Match — this is the exact text behind that audit row."
      : "No match — this text did not produce that hash.");
  };

  if (error) return <><PageHead title="Session replay" /><ErrorBanner error={error} /></>;
  if (!replay) return <><PageHead title="Session replay" /><div className="empty">Loading…</div></>;

  const held = replay.held_actions;

  return (
    <>
      <PageHead title="Session replay">
        <span className="mono">{replay.session_id}</span> — {replay.events.length}{" "}
        decisions, replayable in full months later.
      </PageHead>

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="row" style={{ gap: 8 }}>
            <span className="dot" data-on={replay.integrity.ok} />
            <div>
              <div style={{ fontWeight: 600 }}>
                {replay.integrity.ok ? "Hash chain intact" : "Chain broken"}
              </div>
              <div className="tiny muted">
                {replay.integrity.ok
                  ? `${replay.integrity.events} entries, each committing to its predecessor`
                  : `first broken entry: seq ${replay.integrity.first_broken_seq}`}
              </div>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="stat-value" style={{ fontSize: 21 }}>
            {replay.provenance_graph.nodes.length}
          </div>
          <div className="stat-label">Spans in the provenance graph</div>
          <div className="stat-note">{replay.provenance_graph.edges.length} lineage edge(s)</div>
        </div>
        <div className="card">
          <div className="row-between">
            <div>
              <div className="stat-value" style={{ fontSize: 21 }}>{held.length}</div>
              <div className="stat-label">Actions held</div>
            </div>
            <a href={`${API_BASE}/v1/sessions/${replay.session_id}/evidence`}
               target="_blank" rel="noreferrer"
               className="badge" data-neutral="true" style={{ padding: "5px 9px" }}>
              evidence pack ↗
            </a>
          </div>
          <div className="stat-note">EU AI Act Art. 12 · ISO/IEC 42001</div>
        </div>
      </div>

      {held.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">Held actions in this session</div>
          {held.map((action) => (
            <div key={action.action_id} className="chain" style={{ marginBottom: 8 }}>
              <div className="row-between">
                <strong className="mono">{action.tool}</strong>
                <Verdict value={action.status} />
              </div>
              <div className="tiny muted" style={{ marginTop: 4 }}>{action.reason}</div>
              {action.resolved_by ? (
                <div className="tiny faint" style={{ marginTop: 3 }}>
                  resolved by {action.resolved_by}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">Decision trace</div>
          <div className="timeline">
            {replay.events.map((event) => (
              <div key={event.seq} className="timeline-item">
                <span className="timeline-dot"
                      style={{ background: `var(--${event.verdict})` }} />
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <span className="mono tiny faint">#{event.seq}</span>
                  <Verdict value={event.verdict} />
                  <span className="tiny muted">{LAYER_LABEL[event.layer] ?? event.layer}</span>
                  {event.latency_ms ? (
                    <span className="mono tiny faint">{event.latency_ms.toFixed(2)} ms</span>
                  ) : null}
                </div>
                <div style={{ fontSize: 13, marginTop: 3 }}>{event.reason}</div>
                <div className="mono tiny faint" style={{ marginTop: 3, wordBreak: "break-all" }}>
                  {event.source ? `${event.source} · ` : ""}
                  {event.provenance ? `${event.provenance} · ` : ""}
                  {new Date(event.at).toLocaleTimeString(undefined, { hour12: false })}
                </div>
                {event.span ? (
                  <button style={{ padding: "2px 8px", fontSize: 11, marginTop: 5 }}
                          onClick={() => { setSelectedSpan(event.span); setVerifyResult(null); }}
                          data-variant={selectedSpan === event.span ? "primary" : undefined}>
                    span {event.span.replace("sha256:", "").slice(0, 12)}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-title">Proof without disclosure</div>
            <p className="tiny muted" style={{ marginTop: 0 }}>
              Membrane never stored the text it judged. Select a span in the trace
              and paste a candidate string here: re-hashing proves whether that
              exact text is what produced the row, without the system ever having
              kept a copy of it.
            </p>
            <div className="mono tiny faint" style={{ marginBottom: 8, wordBreak: "break-all" }}>
              {selectedSpan ?? "no span selected"}
            </div>
            <textarea rows={4} value={verifying} placeholder="Paste the suspected payload…"
                      onChange={(e) => { setVerifying(e.target.value); setVerifyResult(null); }} />
            <button style={{ marginTop: 9 }} disabled={!selectedSpan || !verifying}
                    onClick={verify} data-variant="primary">
              Verify against the audit row
            </button>
            {verifyResult ? (
              <div className="chain" style={{ marginTop: 10 }}>{verifyResult}</div>
            ) : null}
          </div>

          <div className="card">
            <div className="card-title">Provenance graph</div>
            {replay.provenance_graph.nodes.length === 0 ? (
              <div className="empty">No spans recorded.</div>
            ) : (
              <div style={{ maxHeight: 300, overflowY: "auto" }}>
                {replay.provenance_graph.nodes.slice(0, 40).map((node) => (
                  <div key={node.span} className="arg-row">
                    <span>{node.span.replace("sha256:", "").slice(0, 14)}</span>
                    <span className="muted">
                      {node.provenance} · {node.source || "—"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <Link href="/sessions" className="tiny" style={{ color: "var(--brand)" }}>
          ← all sessions
        </Link>
      </div>
    </>
  );
}
