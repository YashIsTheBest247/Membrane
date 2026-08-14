"""Render the app mark to transparent PNGs for the deck."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent
_RAW = (Path(__file__).parents[2] / "apps" / "web" / "app" / "icon.svg").read_text(encoding="utf-8")
# The source carries width/height for favicon use; drop them so it fills the box.
SVG = _RAW.replace(' width="48" height="48"', ' width="100%" height="100%"')

PAGE = """
<html><body style="margin:0;background:transparent">
<div id="m" style="width:{size}px;height:{size}px">{svg}</div>
</body></html>
"""


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for size, name in ((512, "logo-512.png"), (128, "logo-128.png")):
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(PAGE.format(size=size, svg=SVG))
            page.wait_for_timeout(250)
            page.locator("#m").screenshot(path=str(OUT / name), omit_background=True)
            print(f"rendered {name}")
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
