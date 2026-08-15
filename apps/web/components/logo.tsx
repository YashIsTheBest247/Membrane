/**
 * The Membrane mark.
 *
 * A rounded square carrying the thesis as one gesture: two faint points
 * approach a dashed vertical barrier, and one solid point has come through it,
 * larger and brighter than what went in.
 *
 * The barrier is dashed rather than solid because the whole point is that it is
 * *semi*-permeable: a wall would starve the cell.
 *
 * The previous mark stacked three horizontal rules behind the barrier, which at
 * favicon size read as a settings or equaliser glyph. One axis of motion, not
 * three, is what keeps this legible at 16px.
 */

export function MembraneMark({
  size = 28,
  inverted = false,
}: {
  size?: number;
  inverted?: boolean;
}) {
  const ground = inverted ? "#17171b" : "#ff3b3b";
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none"
         role="img" aria-label="Membrane">
      <rect width="48" height="48" rx="12" fill={ground} />

      {/* what goes in */}
      <circle cx="9.5" cy="24" r="2" fill="#fff" fillOpacity="0.4" />
      <circle cx="16" cy="24" r="2.6" fill="#fff" fillOpacity="0.66" />

      {/* the semi-permeable barrier */}
      <path d="M24 9V39" stroke="#fff" strokeWidth="3.2" strokeLinecap="round"
            strokeDasharray="4.5 4.2" />

      {/* what comes out */}
      <circle cx="35" cy="24" r="4.4" fill="#fff" />
    </svg>
  );
}

export function MembraneLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="logo">
      <MembraneMark size={compact ? 24 : 28} />
      <span className="logo-name">Membrane</span>
    </div>
  );
}
