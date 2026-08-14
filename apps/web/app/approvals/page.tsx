"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, HeldAction, subscribe } from "@/lib/api";
import { ErrorBanner, PageHead, Verdict } from "@/components/shell";

function Countdown({ expiresAt }: { expiresAt: string }) {
  const [left, setLeft] = useState(() =>
    Math.max(0, (new Date(expiresAt).getTime() - Date.now()) / 1000));

  useEffect(() => {
    const timer = setInterval(() => {
      setLeft(Math.max(0, (new Date(expiresAt).getTime() - Date.now()) / 1000));
    }, 250);
    return () => clearInterval(timer);
  }, [expiresAt]);

  if (left <= 0) return <span className="countdown">expired — denied</span>;
  return <span className="countdown">{left.toFixed(0)}s until this is denied</span>;
}

function Card({ action, onResolved }: {
  action: HeldAction;
  onResolved: (updated: HeldAction) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const card = action.card;

  const decide = async (decision: "approve" | "deny") => {
    setBusy(true);
    setFailed(null);
    try {
      onResolved(await api.decide(action.action_id, decision));
    } catch (exc) {
      setFailed(exc instanceof Error ? exc.message : String(exc));
      setBusy(false);
    }
  };

  return (
    <div className="decision">
      <div className="decision-head">
        <div className="row-between">
          <div className="row">
            <Verdict value="hold" />
            <strong className="mono">{card?.what?.tool ?? action.tool}</strong>
            <span className="badge" data-neutral="true">
              {card?.what?.sensitivity ?? action.sensitivity}
            </span>
          </div>
          <Countdown expiresAt={action.expires_at} />
        </div>
      </div>

      <div className="decision-body">
        <div>
          <div className="decision-section-title">Why it was held</div>
          <div>{card?.why ?? action.reason}</div>
        </div>

        <div>
          <div className="decision-section-title">
            What the agent is attempting
          </div>
          {(card?.what?.arguments ?? []).map((argument) => (
            <div key={argument.name} className="arg-row" data-tainted={argument.tainted}>
              <span>{argument.name}</span>
              <span className="muted">
                {argument.shape}{argument.tainted ? " · tainted" : ""}
              </span>
            </div>
          ))}
          <div className="tiny faint" style={{ marginTop: 6 }}>
            Argument values are reduced to shapes and hashes. The card shows the
            destination domain, because the destination is the decision.
          </div>
        </div>

        {card?.provenance?.length ? (
          <div>
            <div className="decision-section-title">Provenance of the tainted argument</div>
            {card.provenance.map((entry) => (
              <div key={entry.argument} className="chain">
                <strong>{entry.argument}</strong> ← {entry.chain}
                {entry.lineage?.slice(0, 2).map((hop, index) => (
                  <div key={index} className="faint" style={{ marginTop: 3 }}>
                    ↳ {hop.provenance} · {hop.source || "unknown source"} · {hop.match}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : null}

        <div>
          <div className="decision-section-title">Diff against the signed intent</div>
          <div className="chain">
            authorised: {card?.diff?.authorised_capabilities?.join(", ") || "none"}
          </div>
          <div className="chain">
            attempted: {card?.diff?.attempted_capability ?? action.capability}
            {"  ·  inside envelope: "}
            <strong style={{
              color: card?.diff?.in_envelope ? "var(--pass)" : "var(--block)",
            }}>
              {card?.diff?.in_envelope ? "yes" : "no"}
            </strong>
          </div>
        </div>

        {card?.egress?.length ? (
          <div>
            <div className="decision-section-title">Egress findings</div>
            <div className="wrap">
              {card.egress.map((finding, index) => (
                <span key={index} className="badge" data-verdict="block">
                  {finding.kind}: {finding.detail}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="tiny faint">
          session{" "}
          <Link href={`/sessions/${action.session_id}`} style={{ color: "var(--brand)" }}>
            {action.session_id}
          </Link>
          {" · card "}{action.action_id}
          {action.telegram_delivered ? " · delivered to Telegram" : " · Telegram not configured"}
        </div>

        {failed ? <div className="error-banner">{failed}</div> : null}
      </div>

      <div className="decision-actions">
        <button data-variant="approve" disabled={busy} onClick={() => decide("approve")}>
          Approve once
        </button>
        <button data-variant="deny" disabled={busy} onClick={() => decide("deny")}>
          Deny
        </button>
      </div>
    </div>
  );
}

export default function ApprovalsPage() {
  const [pending, setPending] = useState<HeldAction[]>([]);
  const [recent, setRecent] = useState<HeldAction[]>([]);
  const [telegram, setTelegram] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(() => {
    api.pending()
      .then((data) => {
        setPending(data.pending);
        setTelegram(data.telegram_configured);
        setError(null);
      })
      .catch(setError);
    api.recentApprovals().then((data) => setRecent(data.actions)).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4_000);
    const unsubscribe = subscribe((event) => {
      if (["action.held", "approval.resolved", "approval.expired"].includes(event.kind)) {
        refresh();
      }
    });
    return () => { clearInterval(timer); unsubscribe(); };
  }, [refresh]);

  return (
    <>
      <PageHead title="Held actions">
        When Membrane is uncertain it does not guess: it freezes the action and
        asks. Every card here is also on Telegram when a bot is configured, and
        a card nobody answers expires to a denial.
      </PageHead>

      {error ? <ErrorBanner error={error} /> : null}

      {!telegram && (
        <div className="notice" style={{ marginBottom: 16 }}>
          No Telegram bot is configured, so decisions are resolved here. Set{" "}
          <span className="mono">MEMBRANE_TELEGRAM_BOT_TOKEN</span> and{" "}
          <span className="mono">MEMBRANE_TELEGRAM_CHAT_ID</span> to push these cards
          to a phone instead.
        </div>
      )}

      <div className="stack" style={{ marginBottom: 26 }}>
        {pending.length === 0 ? (
          <div className="card"><div className="empty">Nothing is waiting on a human.</div></div>
        ) : pending.map((action) => (
          <Card key={action.action_id} action={action}
                onResolved={() => refresh()} />
        ))}
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--hairline)" }}>
          <div className="card-title" style={{ margin: 0 }}>Decision history</div>
        </div>
        {recent.length === 0 ? <div className="empty">No decisions recorded yet.</div> : (
          <table>
            <thead>
              <tr>
                <th>Status</th><th>Tool</th><th>Capability</th><th>Why</th>
                <th>Resolved by</th><th>Session</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((action) => (
                <tr key={action.action_id}>
                  <td><Verdict value={action.status} /></td>
                  <td className="mono">{action.tool}</td>
                  <td className="mono tiny">{action.capability}</td>
                  <td className="tiny muted" style={{ maxWidth: 380 }}>{action.reason}</td>
                  <td className="tiny">{action.resolved_by ?? "—"}</td>
                  <td>
                    <Link href={`/sessions/${action.session_id}`}
                          className="mono tiny" style={{ color: "var(--brand)" }}>
                      replay
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
