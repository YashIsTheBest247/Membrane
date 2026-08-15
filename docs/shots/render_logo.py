"""Render the app mark to transparent PNGs for the deck.

Four variants come out of one source, so the mark can never drift between the
favicon and the slides:

    logo-512.png / logo-128.png   full colour, for anywhere
    logo-mono.png                 ink on transparent, for light slides
    logo-mono-rev.png             white on transparent, for dark slides

The mono pair are recoloured from the same SVG rather than drawn again — the
deck is monochrome, and a hand-maintained second copy of the mark is a thing
that goes stale without anyone noticing.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent
_RAW = (Path(__file__).parents[2] / "apps" / "web" / "app" / "icon.svg").read_text(
    encoding="utf-8"
)
# The source carries width/height for favicon use; drop them so it fills the box.
SVG = _RAW.replace(' width="48" height="48"', ' width="100%" height="100%"')

ACCENT = "#ff3b3b"     # the square
MARKS = "#fff"         # the dots and the barrier drawn on it

PAGE = """
<html><body style="margin:0;background:transparent">
<div id="m" style="width:{size}px;height:{size}px">{svg}</div>
</body></html>
"""


def recolour(svg: str, ground: str, marks: str) -> str:
    """Swap the two colours. Ground first, so a shared value cannot collide."""
    return svg.replace(ACCENT, "__GROUND__").replace(MARKS, marks).replace(
        "__GROUND__", ground
    )


VARIANTS = [
    ("logo-512.png", 512, SVG),
    ("logo-128.png", 128, SVG),
    # On a light slide the square carries the ink and the marks knock out white.
    ("logo-mono.png", 512, recolour(SVG, "#111111", "#ffffff")),
    # Reversed for a dark panel: the square is the paper, the marks are the ink.
    ("logo-mono-rev.png", 512, recolour(SVG, "#ffffff", "#111111")),
]


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, size, svg in VARIANTS:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(PAGE.format(size=size, svg=svg))
            page.wait_for_timeout(250)
            page.locator("#m").screenshot(path=str(OUT / name), omit_background=True)
            print(f"rendered {name}")
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
