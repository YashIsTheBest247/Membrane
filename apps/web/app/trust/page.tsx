"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ErrorBanner, PageHead } from "@/components/shell";

interface TrustRow {
  source: string;
  score: number;
  incidents: number;
  clean_spans: number;
  last_incident_at: string | null;
}

interface CorpusEntry {
  span: string;
  family: string;
  hits: number;
  first_seen_at: string;
}

export default function TrustPage() {
  const [sources, setSources] = useState<TrustRow[]>([]);
  const [corpus, setCorpus] = useState<CorpusEntry[]>([]);
  const [families, setFamilies] = useState<Record<string, number>>({});
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.trust().then((data) => alive && setSources(data.sources)).catch((e) => alive && setError(e));
      api.corpus().then((data) => {
        if (!alive) return;
        setCorpus(data.entries);
        setFamilies(data.by_family);
      }).catch(() => undefined);
    };
    load();
    const timer = setInterval(load, 10_000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const colourFor = (score: number) =>
    score < 0.25 ? "var(--block)" : score < 0.6 ? "var(--strip)" : "var(--pass)";

  return (
    <>
      <PageHead title="Source trust and the regression corpus">
        Each domain and tool carries a score that collapses on incident and
        recovers slowly with clean history, so the separator&apos;s thresholds
        tighten around sources that have already attacked. Every payload the
        system stops is normalised, deduplicated and filed as a regression case.
      </PageHead>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="grid grid-2">
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--hairline)" }}>
            <div className="card-title" style={{ margin: 0 }}>
              Least trusted sources
            </div>
          </div>
          {sources.length === 0 ? (
            <div className="empty">No sources scored yet.</div>
          ) : (
            <table>
              <thead>
                <tr><th>Source</th><th>Trust</th><th>Incidents</th><th>Clean spans</th></tr>
              </thead>
              <tbody>
                {sources.map((row) => (
                  <tr key={row.source}>
                    <td className="mono tiny" style={{ maxWidth: 220, wordBreak: "break-all" }}>
                      {row.source}
                    </td>
                    <td style={{ minWidth: 120 }}>
                      <span className="mono" style={{ color: colourFor(row.score) }}>
                        {row.score.toFixed(3)}
                      </span>
                      <div className="bar-track">
                        <div className="bar-fill" style={{
                          width: `${row.score * 100}%`,
                          background: colourFor(row.score),
                        }} />
                      </div>
                    </td>
                    <td className="mono" style={{ color: row.incidents ? "var(--block)" : undefined }}>
                      {row.incidents}
                    </td>
                    <td className="mono faint">{row.clean_spans}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--hairline)" }}>
            <div className="row-between">
              <div className="card-title" style={{ margin: 0 }}>Self-reinforcing corpus</div>
              <span className="tiny faint">{corpus.length} entries</span>
            </div>
          </div>

          {Object.keys(families).length > 0 && (
            <div className="wrap" style={{ padding: "12px 16px 0" }}>
              {Object.entries(families).sort((a, b) => b[1] - a[1]).map(([family, count]) => (
                <span key={family} className="badge" data-neutral="true">
                  {family} {count}
                </span>
              ))}
            </div>
          )}

          {corpus.length === 0 ? (
            <div className="empty">
              Nothing filed yet. Every payload the layers stop lands here
              automatically as a regression case.
            </div>
          ) : (
            <table>
              <thead>
                <tr><th>Span hash</th><th>Family</th><th>Hits</th><th>First seen</th></tr>
              </thead>
              <tbody>
                {corpus.slice(0, 40).map((entry) => (
                  <tr key={entry.span}>
                    <td className="mono tiny">
                      {entry.span.replace("sha256:", "").slice(0, 16)}
                    </td>
                    <td className="tiny">{entry.family}</td>
                    <td className="mono">{entry.hits}</td>
                    <td className="tiny faint">
                      {new Date(entry.first_seen_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="tiny faint" style={{ padding: "12px 16px" }}>
            Only hashes are filed. The corpus grows with every attempted attack
            without ever accumulating the text of one.
          </div>
        </div>
      </div>
    </>
  );
}
