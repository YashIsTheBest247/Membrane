/**
 * Icon set — inline, stroked, 24px grid, no dependency.
 *
 * Everything is a single <path> family on a consistent 1.7 stroke so the icons
 * sit at the same optical weight as the UI text next to them.
 */

type IconProps = { size?: number; className?: string; strokeWidth?: number };

const base = (size: number, strokeWidth: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export type IconName =
  | "pulse" | "shield" | "layers" | "flask" | "plug" | "gauge" | "trophy"
  | "search" | "bell" | "check" | "close" | "clock" | "up" | "down"
  | "external" | "chevron" | "filter" | "spark" | "lock" | "eye" | "hash"
  | "refresh" | "play" | "database" | "alert";

export function Icon({ name, size = 18, strokeWidth = 1.7, className }: IconProps & { name: IconName }) {
  const p = { ...base(size, strokeWidth), className };
  switch (name) {
    case "pulse":
      return <svg {...p}><path d="M3 12h4l3-8 4 16 3-8h4" /></svg>;
    case "shield":
      return <svg {...p}><path d="M12 3l7 3v5.5c0 4.2-2.9 8.1-7 9.5-4.1-1.4-7-5.3-7-9.5V6l7-3z" /><path d="M12 8.5v4" /><path d="M12 15.8v.2" /></svg>;
    case "layers":
      return <svg {...p}><path d="M12 3l8.5 4.5L12 12 3.5 7.5 12 3z" /><path d="M3.5 12L12 16.5 20.5 12" /><path d="M3.5 16.5L12 21l8.5-4.5" /></svg>;
    case "flask":
      return <svg {...p}><path d="M9.5 3v6.2L4.6 17.4A2 2 0 006.3 20.5h11.4a2 2 0 001.7-3.1L14.5 9.2V3" /><path d="M8.5 3h7" /><path d="M7.3 14.5h9.4" /></svg>;
    case "plug":
      return <svg {...p}><path d="M9 3v5" /><path d="M15 3v5" /><path d="M6.5 8h11v3.5a5.5 5.5 0 01-11 0V8z" /><path d="M12 17v4" /></svg>;
    case "gauge":
      return <svg {...p}><path d="M20 15a8 8 0 10-16 0" /><path d="M12 15l4.2-4.2" /><path d="M4 19h16" /></svg>;
    case "trophy":
      return <svg {...p}><path d="M7 4h10v5a5 5 0 01-10 0V4z" /><path d="M7 6H4.5a2.5 2.5 0 005 .5" /><path d="M17 6h2.5a2.5 2.5 0 01-5 .5" /><path d="M12 14v3.5" /><path d="M8.5 20.5h7" /><path d="M10 17.5h4v3h-4z" /></svg>;
    case "search":
      return <svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></svg>;
    case "bell":
      return <svg {...p}><path d="M18 9a6 6 0 10-12 0c0 5-2 6-2 6h16s-2-1-2-6z" /><path d="M10.3 19a2 2 0 003.4 0" /></svg>;
    case "check":
      return <svg {...p}><path d="M4.5 12.5l5 5 10-11" /></svg>;
    case "close":
      return <svg {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>;
    case "clock":
      return <svg {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>;
    case "up":
      return <svg {...p}><path d="M12 19V5" /><path d="M6 11l6-6 6 6" /></svg>;
    case "down":
      return <svg {...p}><path d="M12 5v14" /><path d="M6 13l6 6 6-6" /></svg>;
    case "external":
      return <svg {...p}><path d="M14 4h6v6" /><path d="M20 4l-8.5 8.5" /><path d="M18 14.5V19a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 014 19V8a1.5 1.5 0 011.5-1.5H10" /></svg>;
    case "chevron":
      return <svg {...p}><path d="M9 6l6 6-6 6" /></svg>;
    case "filter":
      return <svg {...p}><path d="M4 6h16" /><path d="M7 12h10" /><path d="M10 18h4" /></svg>;
    case "spark":
      return <svg {...p}><path d="M12 3l1.9 5.4L19 10l-5.1 1.6L12 17l-1.9-5.4L5 10l5.1-1.6L12 3z" /></svg>;
    case "lock":
      return <svg {...p}><rect x="4.5" y="10" width="15" height="10" rx="2" /><path d="M8 10V7.5a4 4 0 018 0V10" /></svg>;
    case "eye":
      return <svg {...p}><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></svg>;
    case "hash":
      return <svg {...p}><path d="M9 4L7 20M17 4l-2 16M4 9h16M3.5 15h16" /></svg>;
    case "refresh":
      return <svg {...p}><path d="M20 11a8 8 0 10-1.6 5.6" /><path d="M20 4.5V11h-6.5" /></svg>;
    case "play":
      return <svg {...p}><path d="M7 4.8l12 7.2-12 7.2V4.8z" /></svg>;
    case "database":
      return <svg {...p}><ellipse cx="12" cy="6" rx="7.5" ry="3" /><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" /><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" /></svg>;
    case "alert":
      return <svg {...p}><path d="M12 4.5l8.5 15h-17l8.5-15z" /><path d="M12 10v4" /><path d="M12 17.3v.2" /></svg>;
  }
}

/** A tinted rounded square holding an icon — the stat-tile motif. */
export function IconChip({
  name, tone = "primary", size = 38,
}: { name: IconName; tone?: string; size?: number }) {
  return (
    <span className="chip" data-tone={tone} style={{ width: size, height: size }}>
      <Icon name={name} size={Math.round(size * 0.5)} />
    </span>
  );
}
