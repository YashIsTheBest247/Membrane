# Pitch deck

**`Membrane_NullDeity_NGH26_132.pptx`** — 15 slides, 16:9, paced for 10 minutes.

Team NullDeity · NGH26_132 · Track 02: Cybersecurity for the Future
2nd NextGen Hackathon — ACM Fremont Chapter, USA, in association with the Soft
Computing Research Society.

Every slide carries **speaker notes with a timing cue** (open the Notes pane, or
present in Presenter View). The timings total 10:00 with about 45 seconds of
slack, because the live demo always overruns.

## Running order

| # | Slide | Budget |
|---|---|---|
| 1 | Title | 0:00 – 0:30 |
| 2 | The problem — two safe things that are unsafe together | 0:30 – 1:20 |
| 3 | Not theoretical — EchoLeak, Slack AI, Bing Chat, OWASP LLM01 | 1:20 – 2:00 |
| 4 | Why the existing defences fail | 2:00 – 2:45 |
| 5 | The core idea — one inversion | 2:45 – 3:20 |
| 6 | **Architecture** (mermaid) | 3:20 – 4:05 |
| 7 | The four layers | 4:05 – 5:00 |
| 8 | L3 in one example — the confused deputy | 5:00 – 5:50 |
| 9 | **Request lifecycle** (mermaid) | 5:50 – 6:20 |
| 10 | **The human loop** (mermaid sequence) | 6:20 – 6:55 |
| 11 | Screenshot — the live console | 6:55 – 7:25 |
| 12 | Screenshot — a held action | 7:25 – 8:00 |
| 13 | Measurement — InjectBench | 8:00 – 8:50 |
| 14 | Privacy, safety, compliance | 8:50 – 9:30 |
| 15 | Status and close | 9:30 – 10:00 |

If you are running short, slides 4 and 14 compress most safely. Do not cut 8 —
the confused-deputy example is the one that lands the technical contribution.

## Rebuilding

```bash
# from repo root, with the API venv active
apps/api/.venv/Scripts/python.exe docs/shots/capture.py      # dashboard screenshots
apps/api/.venv/Scripts/python.exe docs/shots/render_logo.py  # logo PNGs
cd docs/diagrams && npx @mermaid-js/mermaid-cli -i 01-architecture.mmd \
    -o 01-architecture.png -b transparent -s 3               # and 02, 03
cd ../deck && python build_deck.py
python audit_deck.py                                         # layout check
```

`capture.py` shoots the **live** console, so start the proxy and the dashboard
first and seed a few sessions — an empty dashboard photographs badly.

## Files

| File | What it is |
|---|---|
| `build_deck.py` | Slide content and assembly |
| `theme.py` | Design system — palette, backgrounds, chrome, composite blocks |
| `notes.py` | Speaker notes and the timing plan |
| `audit_deck.py` | Layout check: off-slide shapes, probable text overflow |
| `../diagrams/*.mmd` | Mermaid sources for the three diagrams |
| `../shots/*.png` | Screenshots of the running console |

## Design

Editorial minimal. **Every slide is on off-white** `F7F7F5` — there are no dark
slides. The base is black, white and three greys, plus **two accents used only
where they mean something**:

| | | |
|---|---|---|
| `5B52E0` | indigo | The product's own brand, taken from the logo. Section labels, sequence numerals, ring gauges, headline figures. |
| `D8453A` | red | The attack side of a contrast, and the payload lines inside a specimen. Nothing else. |

Because nothing else is coloured, either accent reads instantly — the red text
in a specimen block *is* the injected instruction, and needs no caption saying so.

Hierarchy is scale and space: an 8pt letterspaced label above a 38pt caps
headline, a hairline, then body at 11pt with 1.6 leading. Content never fills
more than about two thirds of a slide. Tables are ruled horizontally, never
boxed.

### Where the visual weight comes from

A minimal deck still needs something to look at, and the reference template
gets that from photography. This one has none and should not pretend to, so the
weight comes from three devices instead:

- **Specimen blocks** — near-black panels of real monospace material: the
  poisoned page with its hidden `opacity:0` div, the vendor contact directory
  that reads as entirely innocent. They carry an image's visual mass, and
  unlike an image they are evidence.
- **Oversized figures** — `100%`, `1.33%`, `33ms` set at 42pt beside thin ring
  gauges, and 42pt statements on the black slides.
- **Bleeding screenshots** — the console and the held-action card run off the
  right edge rather than sitting politely inside the margin.

The two product screenshots stay in their real colours. They are the deck's
photography, and recolouring them would misrepresent the product.

Headlines are set in **Bahnschrift**, which ships with Windows 10/11. On a Mac
PowerPoint will substitute; if you are presenting from macOS, change `DISPLAY`
in `theme.py` to something installed there.

The three mermaid diagrams are rendered in the same greyscale so nothing on a
slide fights anything else.

## Two things to know

**The deck was not visually verified.** There is no PowerPoint or LibreOffice on
the build machine, so it could not be rendered and checked by eye. `audit_deck.py`
confirms every shape sits inside the slide and estimates text overflow, but
**open it once before presenting** — particularly slides 4, 7 and 14, which are
the text-densest.

**The organiser logos are set as text**, not images, because the artwork was not
available to embed. If you want the ACM Fremont Chapter and SCRS marks on the
title slide, drop the PNGs into `docs/shots/` and add two `image_fit(...)` calls
at the bottom of `s01_title`.
