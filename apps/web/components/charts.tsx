"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Chart kit — hand-rolled SVG, no charting dependency.
 *
 * Written rather than imported for three reasons: the bundle stays small, the
 * marks match the rest of the type and colour system exactly, and every chart
 * here is driven by live data whose shape we control.
 *
 * All charts scale with `preserveAspectRatio="none"` and rely on
 * `vector-effect: non-scaling-stroke` so line weights stay honest at any width.
 * Text lives in HTML beside the SVG rather than inside it, so it never
 * stretches.
 */

// --------------------------------------------------------------------------
// geometry helpers
// --------------------------------------------------------------------------

type Point = [number, number];

/** Catmull-Rom → cubic Bezier, so a series of readings reads as a curve. */
function smoothPath(points: Point[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0][0]} ${points[0][1]}`;

  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

function polar(cx: number, cy: number, r: number, degrees: number): Point {
  const rad = ((degrees - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function arcPath(cx: number, cy: number, rOuter: number, rInner: number,
                 start: number, end: number): string {
  const large = end - start > 180 ? 1 : 0;
  const [ox1, oy1] = polar(cx, cy, rOuter, start);
  const [ox2, oy2] = polar(cx, cy, rOuter, end);
  const [ix2, iy2] = polar(cx, cy, rInner, end);
  const [ix1, iy1] = polar(cx, cy, rInner, start);
  return [
    `M ${ox1} ${oy1}`,
    `A ${rOuter} ${rOuter} 0 ${large} 1 ${ox2} ${oy2}`,
    `L ${ix2} ${iy2}`,
    `A ${rInner} ${rInner} 0 ${large} 0 ${ix1} ${iy1}`,
    "Z",
  ].join(" ");
}

let gradientSeq = 0;
const nextId = () => `g${(gradientSeq += 1)}`;

/** SVG ids may not contain whitespace; series labels do. */
const gradientId = (index: number, label: string) =>
  `ar-${index}-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;

// --------------------------------------------------------------------------
// sparkline — the mark inside a stat tile
// --------------------------------------------------------------------------

export function Sparkline({
  values, color = "var(--primary)", height = 34,
}: { values: number[]; color?: string; height?: number }) {
  const id = `sp-${nextId()}`;
  const W = 120;
  const H = 40;
  const data = values.length ? values : [0, 0];
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = max - min || 1;

  // An all-zero window would draw a flat rule along the bottom of the tile,
  // which reads as a stray divider rather than as a chart. Draw nothing.
  if (!data.some((value) => value > 0)) {
    return <span className="spark" style={{ height }} aria-hidden="true" />;
  }

  const points: Point[] = data.map((value, index) => [
    (index / Math.max(1, data.length - 1)) * W,
    H - 3 - ((value - min) / span) * (H - 6),
  ]);

  const line = smoothPath(points);
  const area = `${line} L ${W} ${H} L 0 ${H} Z`;
  const last = points[points.length - 1];

  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         style={{ height }} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.26" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round"
            vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r="2.2" fill={color}
              vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// --------------------------------------------------------------------------
// area chart — activity over time
// --------------------------------------------------------------------------

export interface Series {
  label: string;
  color: string;
  values: number[];
}

export function AreaChart({
  series, labels, height = 232, yUnit = "", tickLabels,
}: {
  series: Series[]; labels: string[]; height?: number; yUnit?: string;
  /** Per-bucket labels used by the hover readout. */
  tickLabels?: string[];
}) {
  const W = 800;
  const H = 260;
  const pad = { top: 14, right: 4, bottom: 4, left: 4 };
  const length = Math.max(...series.map((s) => s.values.length), 1);
  const max = Math.max(1, ...series.flatMap((s) => s.values));

  const gridLines = 4;
  const plotH = H - pad.top - pad.bottom;

  const x = (index: number) =>
    pad.left + (index / Math.max(1, length - 1)) * (W - pad.left - pad.right);
  const y = (value: number) => pad.top + plotH - (value / max) * plotH;

  const [hover, setHover] = useState<number | null>(null);

  // The window is rolling and fed only by the live stream, so on a freshly
  // opened console it is legitimately empty. Drawing axes and a flat line at
  // zero reads as broken; saying so reads as an instruction.
  const empty = !series.some((s) => s.values.some((v) => v > 0));

  const onMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    setHover(Math.max(0, Math.min(length - 1, Math.round(ratio * (length - 1)))));
  };

  if (empty) {
    return (
      <div className="chart">
        <div className="chart-empty" style={{ height }}>
          <span className="chart-empty-title">No activity in this window</span>
          <span className="chart-empty-sub">
            The chart is the last 15 minutes, live over the event stream — it
            fills as traffic arrives. Hit <strong>Run live attack</strong> to
            drive some.
          </span>
        </div>
        <div className="chart-legend">
          {series.map((s) => (
            <span key={s.label} className="legend-item">
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="chart">
      <div className="chart-canvas" style={{ height }}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
             style={{ width: "100%", height: "100%" }} aria-hidden="true">
          <defs>
            {series.map((s, index) => (
              // The id must not contain spaces, or url(#…) silently fails to
              // resolve and the area fills render black.
              <linearGradient key={s.label} id={gradientId(index, s.label)}
                              x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.30" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0.02" />
              </linearGradient>
            ))}
          </defs>

          {Array.from({ length: gridLines + 1 }, (_, i) => {
            const gy = pad.top + (i / gridLines) * plotH;
            return (
              <line key={i} x1={pad.left} y1={gy} x2={W - pad.right} y2={gy}
                    stroke="var(--border)" strokeWidth="1"
                    strokeDasharray={i === gridLines ? "0" : "3 4"}
                    vectorEffect="non-scaling-stroke" />
            );
          })}

          {series.map((s, index) => {
            const points: Point[] = s.values.map((v, i) => [x(i), y(v)]);
            if (points.length === 0) return null;
            const line = smoothPath(points);
            const area = `${line} L ${x(s.values.length - 1)} ${H - pad.bottom} L ${x(0)} ${H - pad.bottom} Z`;
            return (
              <g key={s.label}>
                <path d={area} fill={`url(#${gradientId(index, s.label)})`} />
                <path d={line} fill="none" stroke={s.color} strokeWidth="2"
                      strokeLinecap="round" strokeLinejoin="round"
                      vectorEffect="non-scaling-stroke" />
              </g>
            );
          })}

          {hover !== null && (
            <g>
              <line x1={x(hover)} y1={pad.top} x2={x(hover)} y2={H - pad.bottom}
                    stroke="var(--border-strong)" strokeWidth="1"
                    strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
              {series.map((s) => (
                <circle key={s.label} cx={x(hover)} cy={y(s.values[hover] ?? 0)}
                        r="3.6" fill="var(--surface)" stroke={s.color}
                        strokeWidth="2.4" vectorEffect="non-scaling-stroke" />
              ))}
            </g>
          )}
        </svg>

        <div className="chart-y">
          {Array.from({ length: gridLines + 1 }, (_, i) => (
            <span key={i}>{Math.round(max - (i / gridLines) * max)}{yUnit}</span>
          ))}
        </div>

        {hover !== null && (
          <div className="chart-tip"
               style={{ left: `${(hover / Math.max(1, length - 1)) * 100}%` }}>
            <div className="chart-tip-head">
              {tickLabels?.[hover] ?? `bucket ${hover + 1}`}
            </div>
            {series.map((s) => (
              <div key={s.label} className="chart-tip-row">
                <i style={{ background: s.color }} />
                <span>{s.label}</span>
                <strong>{s.values[hover] ?? 0}</strong>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="chart-x">
        {labels.map((label, index) => (
          <span key={index}>{label}</span>
        ))}
      </div>

      <div className="chart-legend">
        {series.map((s) => (
          <span key={s.label} className="legend-item">
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// donut
// --------------------------------------------------------------------------

export interface Segment {
  label: string;
  value: number;
  color: string;
}

export function Donut({
  segments, size = 190, thickness = 22, centerValue, centerLabel,
}: {
  segments: Segment[];
  size?: number;
  thickness?: number;
  centerValue?: string | number;
  centerLabel?: string;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const cx = 100;
  const cy = 100;
  const rOuter = 92;
  const rInner = rOuter - thickness;
  const gap = total > 0 ? 1.6 : 0;

  let cursor = 0;
  const arcs = segments
    .filter((s) => s.value > 0)
    .map((s) => {
      const sweep = (s.value / total) * 360;
      const start = cursor + gap / 2;
      const end = cursor + sweep - gap / 2;
      cursor += sweep;
      return { ...s, path: arcPath(cx, cy, rOuter, rInner, start, Math.max(start + 0.4, end)) };
    });

  return (
    <div className="donut-wrap">
      <div className="donut" style={{ width: size, height: size }}>
        <svg viewBox="0 0 200 200" width="100%" height="100%" aria-hidden="true">
          <circle cx={cx} cy={cy} r={(rOuter + rInner) / 2} fill="none"
                  stroke="var(--surface-2)" strokeWidth={thickness} />
          {arcs.map((a) => (
            <path key={a.label} d={a.path} fill={a.color} />
          ))}
        </svg>
        <div className="donut-center">
          <span className="donut-value">{centerValue ?? total}</span>
          {centerLabel ? <span className="donut-label">{centerLabel}</span> : null}
        </div>
      </div>

      <div className="donut-legend">
        {segments.map((s) => (
          <div key={s.label} className="donut-legend-row">
            <i style={{ background: s.color }} />
            <span className="donut-legend-label">{s.label}</span>
            <span className="donut-legend-value">{s.value}</span>
            <span className="donut-legend-pct">
              {total ? Math.round((s.value / total) * 100) : 0}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// horizontal bar list
// --------------------------------------------------------------------------

export function BarList({
  rows, unit = "",
}: { rows: { label: string; value: number; color?: string }[]; unit?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="barlist">
      {rows.length === 0 ? <div className="empty">No data yet.</div> : rows.map((row) => (
        <div key={row.label} className="barlist-row">
          <span className="barlist-label">{row.label}</span>
          <span className="barlist-track">
            <span className="barlist-fill"
                  style={{
                    width: `${(row.value / max) * 100}%`,
                    background: row.color ?? "var(--primary)",
                  }} />
          </span>
          <span className="barlist-value">{row.value}{unit}</span>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// radial gauge — a measurement against a budget
// --------------------------------------------------------------------------

export function Gauge({
  value, max, unit = "", label, color = "var(--primary)", size = 168,
}: {
  value: number; max: number; unit?: string; label?: string;
  color?: string; size?: number;
}) {
  const SWEEP = 250;               // three-quarter dial
  const START = -SWEEP / 2;
  const ratio = Math.max(0, Math.min(1, value / max));
  const cx = 100;
  const cy = 100;
  const rOuter = 88;
  const rInner = 68;

  const track = arcPath(cx, cy, rOuter, rInner, START, START + SWEEP);
  const fill = arcPath(cx, cy, rOuter, rInner, START,
                       START + Math.max(1.5, SWEEP * ratio));

  return (
    <div className="gauge" style={{ width: size }}>
      <svg viewBox="0 0 200 200" width="100%" aria-hidden="true">
        <path d={track} fill="var(--surface-2)" />
        <path d={fill} fill={color} />
      </svg>
      <div className="gauge-center">
        <span className="gauge-value">
          {value.toFixed(1)}<span className="gauge-unit">{unit}</span>
        </span>
        {label ? <span className="gauge-label">{label}</span> : null}
      </div>
    </div>
  );
}

/** Bucket timestamped events into a fixed number of equal time slots. */
export function bucketByTime(
  timestamps: number[], buckets = 24, windowSeconds = 60 * 30,
): number[] {
  const now = Date.now() / 1000;
  const start = now - windowSeconds;
  const width = windowSeconds / buckets;
  const out = new Array(buckets).fill(0);
  for (const ts of timestamps) {
    if (ts < start) continue;
    const index = Math.min(buckets - 1, Math.floor((ts - start) / width));
    if (index >= 0) out[index] += 1;
  }
  return out;
}
