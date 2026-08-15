"use client";

/**
 * The front door.
 *
 * A full-bleed hero over the particle field, with the claim broken across it:
 * the half that survives on the left, the half that does not on the right,
 * with the reaching hands between them. Below the fold, the four layers and
 * the measured results — because "scroll to explore" should be true.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { MembraneMark } from "@/components/logo";
import { GlobeCanvas } from "@/components/globe-canvas";
import { playMembraneTransition } from "@/components/transition";
import { Icon } from "@/components/icons";

const LAYERS = [
  {
    key: "L1",
    name: "Sanitiser",
    body: "Strips what a human could never see but a model reads perfectly — " +
      "zero-width characters, Unicode tag blocks, homoglyphs, white-on-white " +
      "text, nested encodings unwrapped to depth.",
  },
  {
    key: "L2",
    name: "Separator",
    body: "Splits retrieved text into what it states and what it commands, " +
      "scored on co-occurrence rather than keywords. Imperative spans are " +
      "quarantined; the narrative passes through untouched.",
  },
  {
    key: "L3",
    name: "Taint tracker",
    body: "Every span carries its origin. Anything derived from retrieved text " +
      "stays tainted through the whole session, so a laundered value cannot " +
      "arrive at a tool call looking clean.",
  },
  {
    key: "L4",
    name: "Capability firewall",
    body: "Tool calls are checked against a signed intent contract. Tainted " +
      "arguments on a privileged capability are held for a human — and " +
      "silence is treated as denial.",
  },
];

const MEASURED = [
  { value: "100%", head: "Attack success reduction", note: "42 of 42 stopped" },
  { value: "1.33%", head: "False positive rate", note: "1 of 75 benign spans" },
  { value: "21.1 ms", head: "Added latency, p95", note: "against a 100 ms budget" },
  { value: "42", head: "Open benchmark cases", note: "across 9 attack families" },
];

const SECTIONS = [
  { id: "top", label: "Home" },
  { id: "layers", label: "Layers" },
  { id: "results", label: "Results" },
];

export default function LandingPage() {
  const router = useRouter();
  const [entering, setEntering] = useState(false);
  const [active, setActive] = useState("top");
  const scope = useRef<HTMLDivElement>(null);

  useEffect(() => { router.prefetch("/dashboard"); }, [router]);

  // Light up the bottom nav for whichever section is under the viewport.
  useEffect(() => {
    const root = scope.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    for (const { id } of SECTIONS) {
      const el = root.querySelector(`#${id}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);

  const enter = useCallback(() => {
    if (entering) return;
    setEntering(true);
    const delay = playMembraneTransition();
    setTimeout(() => router.push("/dashboard"), delay);
  }, [entering, router]);

  return (
    <div className="site" ref={scope}>
      {/* ---------------------------------------------------------------- */}
      <section className="hero" id="top">
        <GlobeCanvas className="hero-canvas" />
        <div className="hero-veil" aria-hidden="true" />

        <header className="hero-top">
          <div className="hero-brand">
            <MembraneMark size={26} />
            <span className="hero-brand-name">Membrane</span>
          </div>
          {/* Deliberately not a second "Enter Membrane" — the hero owns that
              call to action, and repeating it here just splits the click. */}
          <Link href="/leaderboard" className="pill">
            Benchmark
            <span className="pill-arrow"><Icon name="arrow" size={13} strokeWidth={2} /></span>
          </Link>
        </header>

        <div className="hero-body">
          <div className="hero-left">
            <span className="hero-eyebrow">[ &nbsp;Prompt injection&nbsp; ]</span>
            <h1 className="hero-h1">
              Content<br />passes.
            </h1>
            <div className="hero-cta">
              <button className="pill pill-lg pill-solid" onClick={enter} disabled={entering}>
                {entering ? "Entering…" : "Enter Membrane"}
                <span className="pill-arrow"><Icon name="arrow" size={14} strokeWidth={2} /></span>
              </button>
            </div>
          </div>

          {/* Reading order is headline then note; on desktop the note is
              lifted above it with `order`, which is presentation only. */}
          <div className="hero-right">
            <h1 className="hero-h1">
              Instructions<br />don&rsquo;t.
            </h1>
            <p className="hero-note">
              Every page, email and tool response an agent reads is a message
              from a stranger. Membrane separates what a document says from what
              it asks for.
            </p>
          </div>
        </div>

        <footer className="hero-bottom">
          <a className="hero-scroll" href="#layers">
            <Icon name="down" size={13} strokeWidth={2} /> Scroll to explore
          </a>
          <nav className="hero-nav">
            {SECTIONS.map((section) => (
              <a key={section.id} href={`#${section.id}`}
                 className="hero-nav-item" data-active={active === section.id}>
                <span className="sq sq-sm" />
                {section.label}
              </a>
            ))}
            <Link href="/leaderboard" className="hero-nav-item">Benchmark</Link>
          </nav>
          <span className="hero-meta">NullDeity · NGH26_132</span>
        </footer>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="band" id="layers">
        <div className="band-head">
          <span className="tag"><span className="sq sq-sm" />Four layers</span>
          <h2 className="band-title">
            One decision, taken four times, before anything is allowed to happen.
          </h2>
        </div>
        <div className="layer-grid">
          {LAYERS.map((layer) => (
            <article key={layer.key} className="layer">
              <span className="layer-key">{layer.key}</span>
              <h3 className="layer-name">{layer.name}</h3>
              <p className="layer-body">{layer.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section className="band band-tight" id="results">
        <div className="band-head">
          <span className="tag"><span className="sq sq-sm" />InjectBench</span>
          <h2 className="band-title">
            Measured against 42 attacks and 75 benign spans. Reproducible.
          </h2>
        </div>
        <div className="landing-stats">
          {MEASURED.map((stat) => (
            <div key={stat.value} className="landing-stat">
              <div className="landing-stat-value">{stat.value}</div>
              <div className="landing-stat-label">
                <b>{stat.head}</b>
                {stat.note}
              </div>
            </div>
          ))}
        </div>

        <div className="closer">
          <h2 className="closer-title">See it stop one, live.</h2>
          <p className="closer-sub">
            The console runs the confused-deputy scenario against this proxy for
            real — a vendor page carrying a hidden instruction and an
            attacker-controlled billing address.
          </p>
          <div className="closer-actions">
            <button className="pill pill-lg pill-solid" onClick={enter} disabled={entering}>
              {entering ? "Entering…" : "Enter Membrane"}
              <span className="pill-arrow"><Icon name="arrow" size={14} strokeWidth={2} /></span>
            </button>
            <Link href="/leaderboard" className="pill pill-lg">See the benchmark</Link>
          </div>
        </div>
      </section>

      <footer className="site-foot">
        <span>Four layers · sanitiser · separator · taint tracker · capability firewall</span>
        <span>Zero content retention · hash-chained audit</span>
      </footer>
    </div>
  );
}
