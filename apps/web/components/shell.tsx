"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { api, API_BASE, subscribe } from "@/lib/api";
import { MembraneMark } from "@/components/logo";
import { MembraneTransition } from "@/components/transition";
import { Icon, IconName } from "@/components/icons";

const NAV: {
  section: string;
  items: { href: string; label: string; icon: IconName; urgent?: boolean }[];
}[] = [
  {
    section: "Operations",
    items: [
      { href: "/dashboard", label: "Live feed", icon: "pulse" },
      { href: "/approvals", label: "Held actions", icon: "shield", urgent: true },
      { href: "/sessions", label: "Sessions", icon: "layers" },
    ],
  },
  {
    section: "Instruments",
    items: [
      { href: "/playground", label: "Playground", icon: "flask" },
      { href: "/mcp", label: "MCP scanner", icon: "plug" },
      { href: "/trust", label: "Source trust", icon: "gauge" },
      { href: "/leaderboard", label: "InjectBench", icon: "trophy" },
    ],
  },
];

const TITLES: Record<string, string> = {
  "/dashboard": "Live feed",
  "/approvals": "Held actions",
  "/sessions": "Sessions",
  "/playground": "Playground",
  "/mcp": "MCP scanner",
  "/trust": "Source trust",
  "/leaderboard": "InjectBench",
};

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(0);
  const [retained, setRetained] = useState<boolean | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  const isLanding = pathname === "/";

  useEffect(() => {
    if (isLanding) return;
    // The shell keeps its own wire open, so the pending count stays right even
    // while the operator is looking at another page.
    const unsubscribe = subscribe(
      (event) => {
        if (event.kind === "action.held") setPending((n) => n + 1);
        if (event.kind === "approval.resolved" || event.kind === "approval.expired") {
          setPending((n) => Math.max(0, n - 1));
        }
      },
      setConnected,
    );
    return unsubscribe;
  }, [isLanding]);

  useEffect(() => {
    if (isLanding) return;
    let alive = true;
    const poll = () => {
      api.pending().then((d) => alive && setPending(d.pending.length)).catch(() => undefined);
      api.stats().then((d) => alive && setRetained(d.privacy.content_retained)).catch(() => undefined);
    };
    poll();
    const timer = setInterval(poll, 15_000);
    return () => { alive = false; clearInterval(timer); };
  }, [pathname, isLanding]);

  // Following a link on a phone should put the drawer away again.
  useEffect(() => { setNavOpen(false); }, [pathname]);

  // The transition is mounted outside the branch below so that it survives the
  // navigation it triggers — otherwise it would unmount mid-sweep.
  if (isLanding) {
    return <>{children}<MembraneTransition /></>;
  }

  const title = TITLES[pathname]
    ?? (pathname.startsWith("/sessions/") ? "Session replay" : "Membrane");

  return (
    <div className="shell" data-nav={navOpen ? "open" : "closed"}>
      <div className="scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <aside className="sidebar">
        <Link href="/" className="logo">
          <MembraneMark size={30} />
          <span className="logo-name">Membrane</span>
        </Link>

        <nav className="nav">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="nav-section">{group.section}</div>
              {group.items.map((item) => (
                <Link key={item.href} href={item.href} className="nav-link"
                      data-active={pathname === item.href}>
                  <Icon name={item.icon} size={17} />
                  <span className="nav-label">{item.label}</span>
                  {item.urgent && pending > 0 && (
                    <span className="nav-count">{pending}</span>
                  )}
                </Link>
              ))}
            </div>
          ))}

          <div className="nav-section">Pipeline</div>
          <div className="pipeline">
            {([
              ["L1", "Sanitiser"],
              ["L2", "Separator"],
              ["L3", "Taint tracker"],
              ["L4", "Capability firewall"],
            ] as const).map(([key, name]) => (
              <div key={key} className="pipeline-row">
                <span className="pipeline-key">{key}</span>
                <span>{name}</span>
              </div>
            ))}
          </div>
        </nav>

        <div className="sidebar-foot">
          <div className="status-line">
            <span className="dot" data-on={connected} />
            <span>{connected ? "Feed connected" : "Feed disconnected"}</span>
          </div>
          {retained !== null && (
            <div className="status-line">
              <span className="dot" data-on={!retained} />
              <span>{retained ? "Replay retention on" : "Zero content retention"}</span>
            </div>
          )}
          <div className="endpoint">{API_BASE}</div>
        </div>
      </aside>

      <div className="content">
        <header className="topbar">
          <button className="nav-toggle" onClick={() => setNavOpen((v) => !v)}
                  aria-label="Toggle navigation" aria-expanded={navOpen}>
            <Icon name={navOpen ? "close" : "menu"} size={18} />
          </button>

          <span className="topbar-title">{title}</span>

          <span className="topbar-spacer" />

          <label className="searchbox">
            <Icon name="search" size={15} />
            <input placeholder="Search sessions, hashes, sources…"
                   aria-label="Search" />
          </label>

          <span className="topbar-pill" data-live={connected}>
            <span className="dot" data-on={connected} />
            <span>{connected ? "Live" : "Offline"}</span>
          </span>

          <Link href="/approvals" className="icon-button" aria-label="Held actions">
            <Icon name="bell" size={17} />
            {pending > 0 && <span className="ping" />}
          </Link>
        </header>

        <main className="main">{children}</main>
      </div>

      <MembraneTransition />
    </div>
  );
}

export function PageHead({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <header className="page-head">
      <h1 className="page-title">{title}</h1>
      {children && <p className="page-sub">{children}</p>}
    </header>
  );
}

export function Verdict({ value }: { value: string }) {
  return <span className="badge" data-verdict={value}>{value}</span>;
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error-banner">
      <div className="row" style={{ gap: 9, alignItems: "flex-start" }}>
        <Icon name="alert" size={17} />
        <div>
          Could not reach the proxy at <span className="mono">{API_BASE}</span>.{" "}
          {String(error instanceof Error ? error.message : error)}
          <div className="tiny" style={{ marginTop: 6, opacity: 0.85 }}>
            Start it with <span className="mono">make api</span> or{" "}
            <span className="mono">docker compose up</span>.
          </div>
        </div>
      </div>
    </div>
  );
}
