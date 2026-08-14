/**
 * The Membrane mark.
 *
 * A hexagonal badge — the shape security tooling has used since chip dies and
 * shield plates — carrying the thesis as a drawing: three tracks approach a
 * porous vertical barrier. Two pass straight through, which is content. One
 * stops short and terminates in a dot, which is an instruction.
 *
 * The barrier is dashed rather than solid because the whole point is that it is
 * *semi*-permeable: a wall would starve the cell.
 */

export function MembraneMark({ size = 40 }: { size?: number }) {
  const id = `mg-${size}`;
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none"
         role="img" aria-label="Membrane">
      <defs>
        <linearGradient id={id} x1="6" y1="4" x2="42" y2="44"
                        gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#7C6BF5" />
          <stop offset="55%" stopColor="#5B52E0" />
          <stop offset="100%" stopColor="#2F80ED" />
        </linearGradient>
      </defs>

      {/* Filling and stroking the same polygon rounds its corners. */}
      <path d="M24 4 L41.3 14 L41.3 34 L24 44 L6.7 34 L6.7 14 Z"
            fill={`url(#${id})`} stroke={`url(#${id})`} strokeWidth="4.5"
            strokeLinejoin="round" />

      {/* content passes through */}
      <path d="M13 17.6 H35" stroke="#ffffff" strokeOpacity="0.92"
            strokeWidth="2.4" strokeLinecap="round" />
      <path d="M13 30.4 H35" stroke="#ffffff" strokeOpacity="0.92"
            strokeWidth="2.4" strokeLinecap="round" />

      {/* an instruction does not */}
      <path d="M13 24 H20.4" stroke="#FF8FA0" strokeWidth="2.4"
            strokeLinecap="round" />
      <circle cx="22.6" cy="24" r="1.9" fill="#FF8FA0" />

      {/* the semi-permeable barrier */}
      <path d="M27.4 13.4 V34.6" stroke="#ffffff" strokeWidth="3"
            strokeLinecap="round" strokeDasharray="3.6 3.2" />
    </svg>
  );
}

export function MembraneLogo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="logo">
      <MembraneMark size={compact ? 34 : 40} />
      <div className="logo-type">
        <span className="logo-word">Membrane</span>
        <span className="logo-tag">Prompt-injection firewall</span>
      </div>
    </div>
  );
}
