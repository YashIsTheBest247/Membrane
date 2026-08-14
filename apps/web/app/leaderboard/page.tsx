"use client";

import { useEffect, useState } from "react";
import { api, BenchRun } from "@/lib/api";
import { ErrorBanner, PageHead } from "@/components/shell";
import { IconChip } from "@/components/icons";

type Entry = BenchRun & { rank: number };

function Target({ met, children }: { met: boolean; children: React.ReactNode }) {
  return (
    <span className="badge" data-verdict={met ? "pass" : "block"}>
      {met ? "meets target" : "misses target"} · {children}
    </span>
  );
}

export default function LeaderboardPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [targets, setTargets] = useState<Record<string, number>>({});
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.leaderboard()
      .then((data) => { setEntries(data.entries); setTargets(data.targets); })
      .catch(setError);
  }, []);

  const best = entries[0];

  return (
    <>
      <PageHead title="InjectBench">
        A security claim without a number is an anecdote. Forty-two categorised
        attacks, a benign corpus for the false-positive rate, and a reproducible
        harness — with the three figures always reported together, because any
        one of them alone is trivially gamed.
      </PageHead>

      {error ? <ErrorBanner error={error} /> : null}

      {best ? (
        <div className="grid grid-3" style={{ marginBottom: 18 }}>
          <div className="card tile">
            <div className="tile-head">
              <IconChip name="shield" tone="emerald" />
              <Target met={best.meets_targets.asr_reduction}>
                &ge; {((targets.asr_reduction_min ?? 0.9) * 100).toFixed(0)}%
              </Target>
            </div>
            <div className="stat-value" style={{ color: "var(--pass)" }}>
              {(best.asr_reduction * 100).toFixed(1)}%
            </div>
            <div className="stat-label">Attack-success-rate reduction</div>
            <div className="stat-note">
              {best.unprotected_success}/{best.total_cases} unprotected →{" "}
              {best.protected_success}/{best.total_cases} protected
            </div>
          </div>
          <div className="card tile">
            <div className="tile-head">
              <IconChip name="filter" tone="violet" />
              <Target met={best.meets_targets.false_positive_rate}>
                &lt; {((targets.false_positive_rate_max ?? 0.02) * 100).toFixed(0)}%
              </Target>
            </div>
            <div className="stat-value">
              {(best.false_positive_rate * 100).toFixed(2)}%
            </div>
            <div className="stat-label">False positives on benign content</div>
            <div className="stat-note">measured per span, not per document</div>
          </div>
          <div className="card tile">
            <div className="tile-head">
              <IconChip name="gauge" tone="sky" />
              <Target met={best.meets_targets.p95_latency}>
                &lt; {targets.p95_added_latency_ms_max ?? 100} ms
              </Target>
            </div>
            <div className="stat-value">
              {best.p95_latency_ms.toFixed(1)}<span className="stat-unit">ms</span>
            </div>
            <div className="stat-label">Added latency, p95</div>
            <div className="stat-note">p50 {best.p50_latency_ms.toFixed(1)} ms</div>
          </div>
        </div>
      ) : null}

      {best?.detail && typeof best.detail === "object" && "by_family" in best.detail ? (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-title">By attack family</div>
          <table>
            <thead>
              <tr><th>Family</th><th>Cases</th><th>Unprotected</th><th>Protected</th><th>Stopped</th></tr>
            </thead>
            <tbody>
              {Object.entries(
                best.detail.by_family as Record<string, Record<string, number>>,
              ).sort().map(([family, entry]) => (
                <tr key={family}>
                  <td className="mono tiny">{family}</td>
                  <td className="mono">{entry.cases}</td>
                  <td className="mono" style={{ color: "var(--block)" }}>{entry.unprotected}</td>
                  <td className="mono" style={{ color: entry.protected ? "var(--block)" : "var(--pass)" }}>
                    {entry.protected}
                  </td>
                  <td className="mono" style={{ color: "var(--pass)" }}>{entry.prevented}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Array.isArray((best.detail as Record<string, unknown>).failures)
            && ((best.detail as Record<string, string[]>).failures.length > 0) ? (
            <div className="notice" style={{ marginTop: 12 }}>
              Cases this run failed to stop:{" "}
              <span className="mono">
                {(best.detail as Record<string, string[]>).failures.join(", ")}
              </span>
              . We publish the failures because a benchmark that only reports wins
              is a brochure.
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--hairline)" }}>
          <div className="card-title" style={{ margin: 0 }}>All recorded runs</div>
        </div>
        {entries.length === 0 ? (
          <div className="empty">
            No runs recorded yet. Run{" "}
            <span className="mono">python -m injectbench --publish http://localhost:8080</span>
            {" "}from <span className="mono">apps/bench</span>.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>#</th><th>Label</th><th>Cases</th><th>ASR reduction</th>
                <th>FPR</th><th>p95</th><th>When</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.run_id}>
                  <td className="mono">{entry.rank}</td>
                  <td>{entry.label}</td>
                  <td className="mono">{entry.total_cases}</td>
                  <td className="mono" style={{ color: "var(--pass)" }}>
                    {(entry.asr_reduction * 100).toFixed(1)}%
                  </td>
                  <td className="mono">{(entry.false_positive_rate * 100).toFixed(2)}%</td>
                  <td className="mono">{entry.p95_latency_ms.toFixed(1)} ms</td>
                  <td className="tiny faint">
                    {new Date(entry.created_at).toLocaleString(undefined, { hour12: false })}
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
