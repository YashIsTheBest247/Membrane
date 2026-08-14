"""Capture dashboard screenshots for the deck.

Runs against the live console so the images are of the real product with real
verdicts in it, not a mockup. Waits on the charts and the SSE feed to settle
before shooting, since an empty state photographs badly.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
OUT = Path(__file__).parent
VIEWPORT = {"width": 1680, "height": 1050}

SHOTS = [
    # (route, filename, settle_ms, full_page)
    ("/", "01-dashboard.png", 4200, False),
    ("/approvals", "02-held-action.png", 2600, False),
    ("/playground", "03-playground.png", 2200, False),
    ("/leaderboard", "04-injectbench.png", 2200, False),
    ("/mcp", "05-mcp-scanner.png", 2200, False),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,          # retina, so it stays crisp on a projector
            color_scheme="light",
        )
        page = context.new_page()

        for route, filename, settle, full in SHOTS:
            page.goto(f"{BASE}{route}", wait_until="networkidle")
            # Client components fetch after hydration; give the charts and the
            # event stream time to arrive and animate in.
            page.wait_for_timeout(settle)
            page.screenshot(path=str(OUT / filename), full_page=full)
            print(f"captured {filename}")

        # The playground needs a run before it shows the two-channel split.
        page.goto(f"{BASE}/playground", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="Run the layers").click()
        page.wait_for_timeout(2600)
        page.screenshot(path=str(OUT / "03-playground.png"))
        print("captured 03-playground.png (after run)")

        # The MCP scanner likewise.
        page.goto(f"{BASE}/mcp", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="Scan before connecting").click()
        page.wait_for_timeout(2200)
        page.screenshot(path=str(OUT / "05-mcp-scanner.png"))
        print("captured 05-mcp-scanner.png (after scan)")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
