"use client";

/**
 * The hero visual: a slowly rotating Earth, drawn as a point cloud.
 *
 * It is the threat model as a picture. Every page, message and tool response an
 * agent reads comes from somewhere out there, and none of it is trustworthy by
 * default. The globe turns, points shed off its leading edge and stream away,
 * and the whole thing is lit by a single directional source so it reads as a
 * solid body rather than as floating confetti.
 *
 * Everything is drawn, not filmed. Points are distributed by Fibonacci spiral —
 * even coverage with no clumping at the poles — then tested against a set of
 * simplified coastlines to decide land from ocean. Land is dense and warm;
 * ocean is sparse and cold, which is what makes the continents legible at a
 * glance. Each frame the whole set is rotated about a tilted polar axis,
 * perspective-projected, and shaded by Lambert against a fixed light, so there
 * is a real terminator running down the sphere.
 */

import { useEffect, useRef } from "react";

/* --------------------------------------------------------------------------
 * coastlines
 *
 * Simplified to a few dozen vertices each, in [longitude, latitude]. This is
 * cartography for a silhouette, not for navigation: it only has to be
 * recognisable at a couple of hundred pixels across.
 * ------------------------------------------------------------------------ */

type Ring = [number, number][];

const NORTH_AMERICA: Ring = [
  [-168, 66], [-160, 71], [-140, 70], [-125, 70], [-110, 69], [-95, 70],
  [-85, 73], [-78, 73], [-70, 63], [-64, 60], [-56, 52], [-66, 45],
  [-70, 42], [-75, 37], [-81, 31], [-80, 25], [-84, 30], [-89, 29],
  [-94, 29], [-97, 26], [-97, 21], [-91, 19], [-88, 21], [-87, 16],
  [-83, 9], [-79, 9], [-85, 13], [-92, 15], [-96, 16], [-104, 19],
  [-110, 24], [-114, 29], [-117, 33], [-122, 37], [-124, 42], [-124, 48],
  [-133, 55], [-145, 60], [-152, 59], [-162, 58], [-166, 62],
];

const SOUTH_AMERICA: Ring = [
  [-77, 8], [-72, 12], [-62, 10], [-52, 5], [-50, 0], [-44, -2], [-35, -6],
  [-39, -13], [-48, -25], [-53, -33], [-58, -38], [-62, -40], [-65, -45],
  [-68, -52], [-66, -55], [-71, -54], [-75, -50], [-74, -42], [-73, -37],
  [-71, -30], [-70, -23], [-70, -18], [-77, -12], [-81, -6], [-80, -2], [-79, 2],
];

const AFRICA: Ring = [
  [-17, 15], [-16, 20], [-12, 27], [-6, 33], [0, 36], [10, 37], [20, 32],
  [30, 31], [34, 28], [35, 23], [38, 18], [43, 12], [51, 12], [51, 4],
  [42, -2], [40, -10], [40, -16], [35, -22], [32, -28], [26, -34], [18, -34],
  [12, -18], [9, -6], [9, 3], [3, 6], [-4, 5], [-8, 4], [-13, 9],
];

const EURASIA: Ring = [
  [-10, 44], [-2, 43], [-1, 49], [2, 51], [5, 53], [8, 54], [8, 57],
  [11, 58], [13, 55], [19, 55], [21, 56], [24, 60], [21, 63], [17, 69],
  [22, 71], [28, 71], [35, 69], [45, 68], [55, 69], [65, 71], [73, 72],
  [80, 74], [95, 78], [105, 77], [113, 74], [125, 73], [135, 72], [145, 70],
  [155, 70], [162, 68], [170, 67], [177, 65], [178, 63], [170, 60], [163, 58],
  [160, 54], [155, 50], [145, 48], [142, 45], [135, 44], [131, 43], [129, 38],
  [126, 35], [122, 30], [121, 25], [110, 21], [108, 15], [106, 10], [103, 1],
  [100, 6], [98, 10], [94, 16], [91, 22], [87, 21], [80, 15], [77, 8],
  [73, 17], [70, 22], [67, 24], [61, 25], [57, 22], [54, 17], [45, 13],
  [43, 17], [39, 21], [35, 28], [34, 31], [36, 36], [32, 36], [28, 40],
  [26, 38], [23, 38], [19, 40], [16, 38], [12, 38], [15, 44], [13, 45],
  [8, 44], [3, 43], [-2, 36], [-6, 36], [-9, 38],
];

const AUSTRALIA: Ring = [
  [113, -22], [114, -26], [116, -35], [122, -34], [129, -32], [135, -35],
  [140, -38], [146, -39], [150, -37], [153, -28], [153, -25], [146, -19],
  [142, -11], [136, -12], [130, -11], [125, -14], [122, -18],
];

const GREENLAND: Ring = [
  [-45, 60], [-52, 64], [-53, 68], [-56, 72], [-60, 76], [-58, 80],
  [-45, 83], [-25, 82], [-20, 77], [-22, 72], [-30, 68], [-38, 64],
];

const ISLANDS: Ring[] = [
  // British Isles
  [[-5, 50], [-6, 55], [-3, 58], [-2, 56], [0, 53], [1, 51]],
  // Japan
  [[130, 31], [132, 34], [136, 35], [141, 40], [142, 44], [145, 43],
   [140, 38], [137, 36], [133, 33]],
  // Madagascar
  [[43, -12], [50, -15], [49, -22], [45, -25], [44, -20]],
  // New Zealand
  [[166, -46], [168, -44], [172, -43], [174, -41], [177, -38], [175, -36],
   [173, -39], [170, -43]],
  // Sumatra
  [[95, 5], [99, 2], [104, -6], [101, -3], [97, 1]],
  // Borneo
  [[109, 2], [117, 4], [119, -1], [116, -4], [110, -3]],
  // New Guinea
  [[131, -1], [140, -2], [147, -6], [150, -10], [143, -9], [137, -8]],
  // Iceland
  [[-24, 65], [-22, 66], [-16, 66], [-14, 65], [-19, 63], [-22, 64]],
];

const LAND: Ring[] = [
  NORTH_AMERICA, SOUTH_AMERICA, AFRICA, EURASIA, AUSTRALIA, GREENLAND,
  ...ISLANDS,
];

/** Ray casting. Rings are closed implicitly. */
function inRing(lon: number, lat: number, ring: Ring): boolean {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if ((yi > lat) !== (yj > lat) &&
        lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      hit = !hit;
    }
  }
  return hit;
}

/** 0 = ocean, 1 = land, 2 = ice sheet. */
function classify(lon: number, lat: number): 0 | 1 | 2 {
  // The ice sheet, rather than thirty more vertices for a coastline nobody
  // will be able to make out at this size. It is kept separate from land
  // because at full land brightness the band wraps the southern limb and
  // reads as a rim around the globe rather than as a polar cap.
  if (lat < -68) return 2;
  for (const ring of LAND) if (inRing(lon, lat, ring)) return 1;
  return 0;
}

/* --------------------------------------------------------------------------
 * the system
 * ------------------------------------------------------------------------ */

type Phase = "sphere" | "shedding";

interface Particle {
  x: number; y: number;          // screen position
  vx: number; vy: number;
  hx: number; hy: number; hz: number;   // home, on the unit sphere
  land: boolean;
  gain: number;                  // brightness multiplier for its surface type
  jitter: number; jphase: number;
  phase: Phase;
  t: number;
  size: number;
  depth: number;
  heat: number;
}

const FOCAL = 2.15;              // in units of the globe radius
const YAW_PER_MS = 0.000105;     // one rotation in roughly sixty seconds
const TILT = 23.4 * Math.PI / 180;

// A fixed light, up and to the left of the viewer, so the terminator runs down
// the right-hand limb.
const LX = -0.44, LY = 0.36, LZ = -0.82;

export function GlobeCanvas({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    // Transparent, so the atmosphere is the stylesheet's job and the canvas
    // has no ground of its own to show a seam at its edges.
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0, height = 0, dpr = 1;
    let particles: Particle[] = [];
    let raf = 0;
    let running = true;

    let cx = 0, cy = 0, radius = 0;
    let mx = -1e5, my = -1e5;      // pointer, parked until it moves

    let clock = 0;
    let yaw = 2.1;                 // opening on the Atlantic

    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    function build() {
      const box = canvas!.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, box.width);
      height = Math.max(1, box.height);
      canvas!.width = Math.round(width * dpr);
      canvas!.height = Math.round(height * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      const narrow = width < 900;
      cx = width * (narrow ? 0.52 : 0.65);
      cy = height * (narrow ? 0.5 : 0.505);
      radius = Math.min(width * (narrow ? 0.3 : 0.22), height * 0.34, 380);

      const budget = Math.round(
        Math.min(8000, Math.max(1600, (width * height) / 210)),
      );

      // Walk a Fibonacci spiral over the sphere, keeping every land point and
      // a sixth of the ocean. Land ends up roughly six times denser, which is
      // most of what makes the continents read at this size.
      const candidates = Math.round(budget * 2.3);
      const golden = Math.PI * (3 - Math.sqrt(5));
      const built: Particle[] = [];

      for (let i = 0; i < candidates && built.length < budget; i += 1) {
        const y = 1 - (i / (candidates - 1)) * 2;
        const r = Math.sqrt(Math.max(0, 1 - y * y));
        const th = golden * i;
        const x = Math.cos(th) * r;
        const z = Math.sin(th) * r;

        const lat = Math.asin(y) * 180 / Math.PI;
        const lon = Math.atan2(z, x) * 180 / Math.PI;
        const kind = classify(lon, lat);
        if (kind === 0 && Math.random() > 0.17) continue;
        if (kind === 2 && Math.random() > 0.4) continue;
        const land = kind !== 0;

        built.push({
          x: 0, y: 0, vx: 0, vy: 0,
          hx: x, hy: y, hz: z,
          land,
          gain: kind === 2 ? 0.5 : 1,
          // Each point breathes by a fraction of a pixel.
          jitter: rand(0.2, 1.4),
          jphase: Math.random() * Math.PI * 2,
          phase: "sphere", t: 0,
          size: kind === 1
            ? (Math.random() < 0.12 ? rand(1.6, 2.4) : rand(0.9, 1.6))
            : kind === 2 ? rand(0.6, 1.0) : rand(0.5, 0.9),
          depth: 1,
          heat: 0.3,
        });
      }

      particles = built;
      orient();
      for (const p of particles) {
        const [sx, sy] = project(p);
        p.x = sx;
        p.y = sy;
      }
    }

    /* Rotation state, recomputed once per frame rather than per particle. */
    let cosY = 1, sinY = 0, cosT = 1, sinT = 0, breath = 1;

    function orient() {
      cosY = Math.cos(yaw);
      sinY = Math.sin(yaw);
      // Axial tilt, plus a very slow nod so the poles are never quite static.
      const tilt = TILT + 0.06 * Math.sin(clock * 0.00005);
      cosT = Math.cos(tilt);
      sinT = Math.sin(tilt);
      breath = 1 + 0.012 * Math.sin(clock * 0.00035);
    }

    /**
     * Spin about the polar axis, tip the pole toward the viewer, project.
     * Returns [screenX, screenY, scale, lambert, facing].
     */
    function project(p: Particle): [number, number, number, number, number] {
      const r = radius * breath;

      // spin
      const x1 = p.hx * cosY + p.hz * sinY;
      const z1 = -p.hx * sinY + p.hz * cosY;
      // tilt
      const y2 = p.hy * cosT - z1 * sinT;
      const z2 = p.hy * sinT + z1 * cosT;

      const s = FOCAL / (FOCAL + z2);
      // The rotated position doubles as the surface normal on a unit sphere.
      const lambert = Math.max(0, x1 * LX + y2 * LY + z2 * LZ);
      return [cx + x1 * r * s, cy + y2 * r * s, s, lambert, -z2];
    }

    function step(dt: number) {
      orient();

      for (const p of particles) {
        if (p.phase === "shedding") {
          p.t = Math.min(1, p.t + 0.0034 * dt);
          p.x += p.vx * dt;
          p.y += p.vy * dt;
          p.vx *= Math.pow(0.994, dt);
          p.heat += (0.04 - p.heat) * 0.013 * dt;
          if (p.t >= 1) p.phase = "sphere";
          continue;
        }

        const [gx, gy, s, lambert, facing] = project(p);
        p.depth = s;
        const j = p.jitter;
        const wob = clock * 0.0013 + p.jphase;
        // Tight enough to hold the sphere, loose enough to breathe.
        p.x += (gx + Math.sin(wob) * j - p.x) * 0.13 * dt;
        p.y += (gy + Math.cos(wob * 1.3) * j - p.y) * 0.13 * dt;

        // Cursor pushes nearby points aside, gently, and they return.
        const ddx = p.x - mx;
        const ddy = p.y - my;
        const d2 = ddx * ddx + ddy * ddy;
        if (d2 < 26_000) {
          const f = (1 - d2 / 26_000) * 1.1;
          const d = Math.sqrt(d2) || 1;
          p.x += (ddx / d) * f * dt;
          p.y += (ddy / d) * f * dt;
        }

        // Shading: a day side, a terminator, and a far side seen through the
        // body of the globe. Ocean is deliberately held far below land — the
        // continents have to be the thing you see.
        const day = 0.16 + lambert * 1.05;
        const through = facing > 0 ? 1 : 0.22;
        // Foreshortening. Equal surface area compresses into fewer pixels
        // toward the limb, so without this correction the additive blend piles
        // up there and the globe wears a hot ring instead of a soft edge.
        const fore = 0.3 + 0.7 * Math.abs(facing);
        const lit = (p.land ? day * p.gain : day * 0.26) * through * fore;
        p.heat += (Math.min(1, lit) - p.heat) * 0.1 * dt;

        // Occasionally a point lets go of the surface and streams away.
        if (Math.random() < 0.00016 * dt) {
          p.phase = "shedding";
          p.t = 0;
          p.vx = -rand(0.3, 1.2);
          p.vy = rand(-0.3, 0.3);
        }
      }
    }

    function draw() {
      // A partial erase rather than a hard clear — that is what leaves the
      // smear on the shed particles, without laying down an opaque ground.
      ctx!.globalCompositeOperation = "destination-out";
      ctx!.fillStyle = "rgba(0, 0, 0, 0.24)";
      ctx!.fillRect(0, 0, width, height);

      ctx!.globalCompositeOperation = "lighter";
      for (const p of particles) {
        const h = p.heat;
        if (h < 0.035) continue;
        const s = p.size * (0.55 + p.depth * 0.62);
        if (p.land) {
          // Land warms toward coral, never all the way to white — the accent
          // has to stay the colour of the object.
          const g = Math.min(175, Math.round(16 + Math.max(0, h - 0.38) * 340));
          const b = Math.min(155, Math.round(20 + Math.max(0, h - 0.52) * 340));
          ctx!.fillStyle = `rgba(255, ${g}, ${b}, ${0.10 + h * 0.66})`;
        } else {
          // Ocean stays a cold deep red so it reads as the body of the sphere
          // rather than as more coastline.
          ctx!.fillStyle = `rgba(196, 26, 44, ${0.05 + h * 0.5})`;
        }
        ctx!.fillRect(p.x, p.y, s, s);
      }
    }

    let last = performance.now();

    function frame(now: number) {
      if (!running) return;
      // Normalised to 60fps, and clamped so a background tab does not teleport
      // every point when it comes back.
      const elapsed = Math.min(120, now - last);
      const dt = Math.min(3, elapsed / 16.67);
      last = now;

      clock += elapsed;
      yaw += YAW_PER_MS * elapsed;

      step(dt);
      draw();
      raf = requestAnimationFrame(frame);
    }

    function reset() {
      ctx!.globalCompositeOperation = "source-over";
      ctx!.clearRect(0, 0, width, height);
    }

    build();
    reset();

    if (reduced) {
      for (let i = 0; i < 60; i += 1) step(1);
      draw();
    } else {
      raf = requestAnimationFrame(frame);
    }

    const observer = new ResizeObserver(() => { build(); reset(); });
    observer.observe(canvas);

    const onMove = (event: PointerEvent) => {
      const box = canvas.getBoundingClientRect();
      mx = event.clientX - box.left;
      my = event.clientY - box.top;
    };
    const onLeave = () => { mx = -1e5; my = -1e5; };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);

    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!reduced && !running) {
        running = true;
        last = performance.now();
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
