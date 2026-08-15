"use client";

/**
 * The entry sequence: passing through the membrane.
 *
 * It is the product's thesis played once, at full size. Tracks stream toward a
 * dashed vertical barrier; most cross it, one stops dead against it and turns
 * red. Then the barrier itself parts and the two halves sweep off-screen,
 * leaving the console behind them.
 *
 * The component is mounted once in the shell rather than on the landing page,
 * because it has to survive the navigation it triggers — otherwise it would
 * unmount mid-sweep and the halves would never open.
 */

import { useEffect, useRef, useState } from "react";

type Phase = "in" | "open" | "out";

/* Timing. The route is pushed just before the halves part, so the dashboard is
 * already painted underneath by the time it becomes visible. */
const OPEN_AT = 1_120;
const NAVIGATE_AT = 1_020;
const DONE_AT = 2_020;

const listeners = new Set<() => void>();

/** Play the sequence. Returns the delay after which the route should change. */
export function playMembraneTransition(): number {
  listeners.forEach((notify) => notify());
  return NAVIGATE_AT;
}

/* Twelve tracks at irregular heights — a regular comb would read as a logo
 * rather than as traffic. One of them is the payload. */
const TRACKS = [4, 11, 19, 26, 33, 41, 48, 56, 63, 71, 79, 88];
const BLOCKED_INDEX = 6;

export function MembraneTransition() {
  const [phase, setPhase] = useState<Phase | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const start = () => {
      timers.current.forEach(clearTimeout);
      setPhase("in");
      timers.current = [
        setTimeout(() => setPhase("open"), OPEN_AT),
        setTimeout(() => setPhase("out"), OPEN_AT + 120),
        setTimeout(() => setPhase(null), DONE_AT),
      ];
    };
    listeners.add(start);
    return () => {
      listeners.delete(start);
      timers.current.forEach(clearTimeout);
    };
  }, []);

  if (phase === null) return null;

  return (
    <div className="transit" data-phase={phase} aria-hidden="true">
      <div className="transit-half" data-side="left" />
      <div className="transit-half" data-side="right" />

      <div className="transit-stage">
        <div className="transit-barrier" />

        {TRACKS.map((top, index) => {
          const blocked = index === BLOCKED_INDEX;
          return (
            <span
              key={top}
              className="transit-track"
              data-blocked={blocked}
              style={{
                top: `${top}%`,
                // The blocked track stops short of the barrier; the rest cross
                // it and run on to the far edge.
                ["--len" as string]: blocked ? "42%" : `${86 + (index % 3) * 4}%`,
                animationDelay: `${180 + index * 34}ms`,
              }}
            />
          );
        })}

        <span
          className="transit-dot"
          style={{ top: `calc(${TRACKS[BLOCKED_INDEX]}% - 3px)`, left: "42%",
                   animationDelay: `${180 + BLOCKED_INDEX * 34 + 430}ms` }}
        />

        <div className="transit-caption">
          <b>content passes</b> · <s>instructions do not</s>
        </div>
      </div>
    </div>
  );
}
