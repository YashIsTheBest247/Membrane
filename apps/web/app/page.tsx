"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, FeedEvent, Stats, subscribe } from "@/lib/api";
import { DemoStep, runAttackDemo } from "@/lib/demo";
import { ErrorBanner, PageHead, Verdict } from "@/components/shell";
import { Icon, IconChip, IconName } from "@/components/icons";
import { AreaChart, BarList, Donut, Gauge, Sparkline } from "@/components/charts";
import { useCountUp } from "@/components/hooks";

const MAX_FEED = 220;
const BUCKETS = 30;
const WINDOW_SECONDS = 15 * 60;

const VERDICT_COLOR: Record<string, string> = {
  pass: "var(--pass)",
  strip: "var(--strip)",
  hold: "var(--hold)",
  block: "var(--block)",
};

const LAYER_LABEL: Record<string, string> = {
  l1_sanitiser: "L1 sanitiser",
  l2_separator: "L2 separator",
  l3_taint: "L3 taint tracker",
  l4_capability: "L4 capability",
  egress: "Egress inspection",
  mcp_scan: "MCP scanner",
  breaker: "Circuit breaker",
  approval: "Human loop",
};

const EVENT_ICON: Record<string, { icon: IconName; tone: string }> = {
  ingest: { icon: "database", tone: "sky" },
  "attack.blocked": { icon: "shield", tone: "block" },
  "action.held": { icon: "lock", tone: "hold" },
  "action.blocked": { icon: "close", tone: "block" },
  "action.passed": { icon: "check", tone: "emerald" },
  "approval.resolved": { icon: "check", tone: "violet" },
  "approval.expired": { icon: "clock", tone: "rose" },
  "breaker.tripped": { icon: "alert", tone: "block" },
  "mcp.scanned": { icon: "plug", tone: "teal" },
  "bench.completed": { icon: "trophy", tone: "amber" },
};

function timeOf(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false });
}

function verdictOf(event: FeedEvent): string {
  if (event.verdict) return event.verdict;
  if (event.kind === "approval.resolved") return event.status === "approved" ? "pass" : "block";
  if (event.kind === "approval.expired" || event.kind === "breaker.tripped") return "block";
  return "pass";
}

function describe(event: FeedEvent): string {
  switch (event.kind) {
    case "ingest":
      return event.quarantined || event.removed
        ? `${event.quarantined ?? 0} span(s) quarantined · ${event.removed ?? 0} hidden region(s) removed`
        : `${event.spans ?? 0} span(s) passed through clean`;
    case "attack.blocked": return event.reason ?? "payload removed";
    case "action.held": return event.reason ?? "action held for a human decision";
    case "action.blocked": return event.reason ?? "action refused";
    case "action.passed": return event.reason ?? "action authorised";
    case "approval.resolved":
      return `${event.status} by ${(event as { resolved_by?: string }).resolved_by ?? "operator"}`;
    case "approval.expired": return "no decision within the timeout — treated as denial";
    case "breaker.tripped": return "circuit breaker tripped: privileged capabilities quarantined";
    case "mcp.scanned": return `${event.server}: ${event.reason ?? ""}`;
    case "bench.completed": return "InjectBench run recorded";
    default: return event.reason ?? event.kind;
  }
}

/** Bucket event timestamps into the rolling window. */
function bucket(events: FeedEvent[], predicate: (e: FeedEvent) => boolean): number[] {
  const now = Date.now() / 1000;
  const start = now - WINDOW_SECONDS;
  const width = WINDOW_SECONDS / BUCKETS;
  const out = new Array(BUCKETS).fill(0);
  for (const event of events) {
    if (!predicate(event) || event.ts < start) continue;
    const index = Math.min(BUCKETS - 1, Math.floor((event.ts - start) / width));
    if (index >= 0) out[index] += 1;
  }
  return out;
}

function Tile({
  icon, tone, value, label, note, spark, sparkColor, delta,
}: {
  icon: IconName; tone: string; value: number; label: string; note?: React.ReactNode;
  spark: number[]; sparkColor: string; delta?: { value: string; dir: "up" | "down" | "flat" };
}) {
  const animated = useCountUp(value);
  return (
    <div className="card tile">
      <div className="tile-head">
        <IconChip name={icon} tone={tone} />
        {delta ? (
          <span className="delta" data-dir={delta.dir}>
            {delta.dir !== "flat" && <Icon name={delta.dir} size={12} strokeWidth={2.2} />}
            {delta.value}
          </span>
        ) : null}
      </div>
      <div className="stat-value">{Math.round(animated).toLocaleString()}</div>
      <div className="stat-label">{label}</div>
      {note ? <div className="stat-note">{note}</div> : null}
      <Sparkline values={spark} color={sparkColor} />
    </div>
  );
}

export default function DashboardPage() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [filter, setFilter] = useState<string>("all");
  const [demoSteps, setDemoSteps] = useState<DemoStep[]>([]);
  const [demoRunning, setDemoRunning] = useState(false);
  const [demoSession, setDemoSession] = useState<string | null>(null);
  const seeded = useRef(false);

  const onEvent = useCallback((event: FeedEvent) => {
    setEvents((current) => [event, ...current].slice(0, MAX_FEED));
  }, []);

  useEffect(() => subscribe(onEvent), [onEvent]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.stats().then((d) => { if (alive) { setStats(d); setError(null); } })
        .catch((e) => alive && setError(e));
      if (!seeded.current) {
        seeded.current = true;
        api.recentFeed().then((d) => alive && setEvents(d.events)).catch(() => undefined);
      }
    };
    load();
    const timer = setInterval(load, 8_000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const runDemo = async () => {
    setDemoRunning(true);
    setDemoSteps([]);
    setDemoSession(null);
    try {
      const session = await runAttackDemo((step) =>
        setDemoSteps((current) => [...current, step]));
      setDemoSession(session);
    } catch (exc) {
      setDemoSteps((current) => [...current, {
        label: "Demo failed",
        detail: exc instanceof Error ? exc.message : String(exc),
        tone: "block",
      }]);
    } finally {
      setDemoRunning(false);
    }
  };

  const verdicts = stats?.by_verdict ?? {};
  const stopped = (verdicts.block ?? 0) + (verdicts.strip ?? 0);

  const series = useMemo(() => [
    {
      label: "Content ingested",
      color: "var(--sky)",
      values: bucket(events, (e) => e.kind === "ingest"),
    },
    {
      label: "Payloads stopped",
      color: "var(--block)",
      values: bucket(events, (e) => e.kind === "attack.blocked" || e.kind === "action.blocked"),
    },
    {
      label: "Held for a human",
      color: "var(--hold)",
      values: bucket(events, (e) => e.kind === "action.held"),
    },
  ], [events]);

  const tickLabels = useMemo(() => {
    const now = Date.now();
    const width = (WINDOW_SECONDS * 1000) / BUCKETS;
    return Array.from({ length: BUCKETS }, (_, i) =>
      new Date(now - (BUCKETS - 1 - i) * width)
        .toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events.length]);

  const donutSegments = (["pass", "strip", "hold", "block"] as const).map((v) => ({
    label: v,
    value: verdicts[v] ?? 0,
    color: VERDICT_COLOR[v],
  }));

  const layerRows = Object.entries(stats?.by_layer ?? {})
    .map(([layer, count]) => ({
      label: LAYER_LABEL[layer] ?? layer,
      value: count as number,
      color: layer.startsWith("l1") ? "var(--sky)"
        : layer.startsWith("l2") ? "var(--violet)"
        : layer.startsWith("l3") ? "var(--teal)"
        : layer.startsWith("l4") ? "var(--primary)"
        : layer === "egress" ? "var(--amber)"
        : "var(--rose)",
    }))
    .sort((a, b) => b.value - a.value);

  const shown = events.filter((event) =>
    filter === "all" ? true
      : filter === "attacks"
        ? ["attack.blocked", "action.held", "action.blocked", "breaker.tripped"].includes(event.kind)
        : verdictOf(event) === filter);

  const p95 = stats?.latency_ms.p95 ?? 0;

  return (
    <>
      <div className="row-between" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        <PageHead title="Live feed">
          Every verdict the proxy reaches, as it reaches it. Previews come from
          memory and are never written to the audit table.
        </PageHead>
        <button data-variant="primary" onClick={runDemo} disabled={demoRunning}>
          <Icon name={demoRunning ? "refresh" : "play"} size={15} strokeWidth={2} />
          {demoRunning ? "Running attack…" : "Run live attack"}
        </button>
      </div>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Tile icon="shield" tone="block" value={stopped} label="Payloads stopped"
              sparkColor="var(--block)"
              spark={bucket(events, (e) => e.kind === "attack.blocked")}
              note={<>
                <span style={{ color: "var(--strip)" }}>{verdicts.strip ?? 0} stripped</span>
                {" · "}
                <span style={{ color: "var(--block)" }}>{verdicts.block ?? 0} blocked</span>
              </>} />

        <Tile icon="lock" tone="hold" value={verdicts.hold ?? 0}
              label="Held for a human" sparkColor="var(--hold)"
              spark={bucket(events, (e) => e.kind === "action.held")}
              note="silence is treated as denial" />

        <Tile icon="layers" tone="violet" value={stats?.sessions ?? 0}
              label="Sessions audited" sparkColor="var(--violet)"
              spark={bucket(events, (e) => e.kind === "ingest")}
              note={`${stats?.corpus_entries ?? 0} payloads in the regression corpus`} />

        <Tile icon="pulse" tone="teal" value={stats?.events ?? 0}
              label="Decisions recorded" sparkColor="var(--teal)"
              spark={bucket(events, () => true)}
              note={`across the last ${stats?.window_hours ?? 24} hours`} />
      </div>

      <div className="grid grid-wide" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="row-between" style={{ marginBottom: 6 }}>
            <div>
              <div className="card-title" style={{ marginBottom: 2 }}>Pipeline activity</div>
              <div className="card-sub">Rolling 15 minutes, live over server-sent events</div>
            </div>
            <span className="topbar-pill" data-live={true}>
              <span className="dot" data-on={true} /> streaming
            </span>
          </div>
          <AreaChart series={series} tickLabels={tickLabels}
                     labels={["15m ago", "10m", "5m", "now"]} height={228} />
        </div>

        <div className="card">
          <div className="card-title" style={{ marginBottom: 2 }}>Verdict mix</div>
          <div className="card-sub" style={{ marginBottom: 16 }}>
            Last {stats?.window_hours ?? 24} hours
          </div>
          <Donut segments={donutSegments} centerValue={stats?.events ?? 0}
                 centerLabel="decisions" />
        </div>
      </div>

      <div className="grid grid-3" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-title">Which layer stopped it</div>
          <BarList rows={layerRows} />
        </div>

        <div className="card">
          <div className="card-title">Added latency</div>
          <Gauge value={p95} max={100} unit="ms" label="p95 · budget 100 ms"
                 color={p95 > 100 ? "var(--block)" : "var(--emerald)"} />
          <div className="row-between" style={{ marginTop: 14 }}>
            {([["p50", stats?.latency_ms.p50], ["p95", stats?.latency_ms.p95],
               ["p99", stats?.latency_ms.p99]] as const).map(([label, value]) => (
              <div key={label} style={{ textAlign: "center", flex: 1 }}>
                <div className="mono" style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
                  {typeof value === "number" ? value.toFixed(1) : "—"}
                </div>
                <div className="tiny faint">{label}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="row-between" style={{ marginBottom: 12 }}>
            <div className="card-title" style={{ margin: 0 }}>Attack demonstration</div>
            {demoSession ? (
              <Link href={`/sessions/${demoSession}`} className="tiny"
                    style={{ color: "var(--primary)", fontWeight: 600 }}>
                replay →
              </Link>
            ) : null}
          </div>

          {demoSteps.length === 0 ? (
            <div className="tiny muted" style={{ lineHeight: 1.65 }}>
              Runs the confused-deputy scenario against this proxy for real: a
              vendor page carrying a hidden instruction <em>and</em> an
              attacker-controlled billing address. Watch the feed and the
              held-action badge react.
              <div style={{ marginTop: 12 }}>
                <button data-size="sm" onClick={runDemo} disabled={demoRunning}>
                  <Icon name="play" size={13} strokeWidth={2} /> Run it
                </button>
              </div>
            </div>
          ) : (
            <div className="steps">
              {demoSteps.map((step, index) => (
                <div key={index} className="step" data-tone={step.tone}>
                  <span className="step-dot" />
                  <div style={{ minWidth: 0 }}>
                    <div className="step-label">
                      {step.label}
                      {step.verdict ? <Verdict value={step.verdict} /> : null}
                    </div>
                    <div className="step-detail">{step.detail}</div>
                  </div>
                </div>
              ))}
              {demoRunning ? <div className="step-running">running…</div> : null}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div className="row-between"
             style={{ padding: "15px 18px", borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="card-title" style={{ margin: 0 }}>Event stream</div>
            <div className="card-sub">{shown.length} shown · {events.length} buffered</div>
          </div>
          <div className="wrap">
            {[["all", "All"], ["attacks", "Attacks"], ["hold", "Held"],
              ["block", "Blocked"], ["pass", "Passed"]].map(([value, label]) => (
              <button key={value} data-size="sm" onClick={() => setFilter(value)}
                      data-variant={filter === value ? "primary" : undefined}>
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="feed">
          {shown.length === 0 ? (
            <div className="empty">
              Nothing yet — hit <strong>Run live attack</strong> above, or send a page
              through <Link href="/playground" style={{ color: "var(--primary)" }}>the playground</Link>.
            </div>
          ) : shown.map((event, index) => {
            const meta = EVENT_ICON[event.kind] ?? { icon: "pulse" as IconName, tone: "sky" };
            return (
              <div key={`${event.seq}-${event.ts}-${index}`} className="feed-item"
                   data-fresh={index === 0}>
                <span className="feed-icon chip" data-tone={meta.tone}>
                  <Icon name={meta.icon} size={15} />
                </span>
                <span className="feed-time">{timeOf(event.ts)}</span>
                <Verdict value={verdictOf(event)} />
                <div className="feed-body">
                  <div className="feed-reason">{describe(event)}</div>
                  <div className="feed-meta">
                    {event.layer ?? event.kind}
                    {event.source ? ` · ${event.source}` : ""}
                    {event.tool ? ` · ${event.tool}` : ""}
                    {event.family ? ` · ${event.family}` : ""}
                    {event.span ? ` · ${event.span.replace("sha256:", "").slice(0, 12)}` : ""}
                  </div>
                  {event.preview ? <div className="feed-preview">{event.preview}</div> : null}
                </div>
                <div style={{ textAlign: "right" }}>
                  {event.session_id ? (
                    <Link href={`/sessions/${event.session_id}`} className="tiny"
                          style={{ color: "var(--primary)", fontWeight: 600 }}>
                      replay
                    </Link>
                  ) : null}
                  {typeof event.latency_ms === "number" ? (
                    <div className="mono tiny faint">{event.latency_ms.toFixed(1)} ms</div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
