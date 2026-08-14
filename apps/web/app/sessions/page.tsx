"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, SessionRow } from "@/lib/api";
import { ErrorBanner, PageHead } from "@/components/shell";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.sessions()
      .then((data) => { if (alive) { setSessions(data.sessions); setError(null); } })
      .catch((exc) => alive && setError(exc));
    load();
    const timer = setInterval(load, 8_000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  return (
    <>
      <PageHead title="Sessions">
        Every session can be replayed with its full decision trace: what was
        blocked, by which layer, and why. The trace holds hashes and provenance,
        never the content it judged.
      </PageHead>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="card" style={{ padding: 0 }}>
        {sessions.length === 0 ? (
          <div className="empty">No sessions recorded yet.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Session</th><th>Subject</th><th>Spans</th><th>Held</th>
                <th>Blocked</th><th>Breaker</th><th>Last seen</th><th></th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.session_id}>
                  <td className="mono">{session.session_id}</td>
                  <td className="tiny muted">{session.subject}</td>
                  <td className="mono">{session.spans_seen}</td>
                  <td className="mono" style={{ color: session.holds ? "var(--hold)" : undefined }}>
                    {session.holds}
                  </td>
                  <td className="mono" style={{ color: session.blocks ? "var(--block)" : undefined }}>
                    {session.blocks}
                  </td>
                  <td>
                    {session.breaker_open
                      ? <span className="badge" data-verdict="block">open</span>
                      : <span className="tiny faint">closed</span>}
                  </td>
                  <td className="tiny faint">
                    {new Date(session.last_seen_at).toLocaleString(undefined, { hour12: false })}
                  </td>
                  <td>
                    <Link href={`/sessions/${session.session_id}`}
                          className="mono tiny" style={{ color: "var(--brand)" }}>
                      replay →
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
