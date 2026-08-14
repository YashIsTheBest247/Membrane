"""L1 — Sanitiser: remove what the user cannot see.

Injection payloads overwhelmingly live in regions a human reader never
renders. This layer strips them deterministically, before any model is
invoked, so the cheapest tier removes the largest class of attacks.

Everything removed is kept in memory for the remainder of the request so the
later layers can classify what was attempted, and is then discarded. Only
salted hashes reach storage.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import html as html_module
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field

from ..signals import score_text

# --------------------------------------------------------------------------
# invisible characters
# --------------------------------------------------------------------------

# Format/control characters that render as nothing but tokenise as something.
INVISIBLE_CHARS = {
    "­",  # soft hyphen
    "͏",  # combining grapheme joiner
    "؜",  # arabic letter mark
    "ᅟ", "ᅠ",  # hangul fillers
    "឴", "឵",  # khmer vowel inherent
    "᠎",  # mongolian vowel separator
    "​", "‌", "‍", "‎", "‏",  # zero width + marks
    "‪", "‫", "‬", "‭", "‮",  # bidi overrides
    "⁠", "⁡", "⁢", "⁣", "⁤",  # word joiner, invisibles
    "⁦", "⁧", "⁨", "⁩",  # bidi isolates
    "⁪", "⁫", "⁬", "⁭", "⁮", "⁯",
    "ㅤ",  # hangul filler
    "︀", "︁", "︂", "︃", "︄", "︅",
    "︆", "︇", "︈", "︉", "︊", "︋",
    "︌", "︍", "︎", "️",  # variation selectors
    "﻿",  # BOM / zero width no-break space
    "ﾠ",  # halfwidth hangul filler
}

# Unicode tag characters (U+E0000–U+E007F). These mirror ASCII invisibly and
# are the cleanest way to hide a full sentence inside a single emoji.
TAG_RANGE = range(0xE0000, 0xE0080)

# Selectors U+E0100–U+E01EF.
VARIATION_SUPPLEMENT = range(0xE0100, 0xE01F0)

# Homoglyph folding. NFKC handles most compatibility forms but not
# cross-script lookalikes, which is exactly what the attacks use.
CONFUSABLES = {
    # Cyrillic → Latin
    "а": "a", "б": "b", "в": "b", "г": "r", "д": "d", "е": "e", "ѐ": "e",
    "з": "3", "и": "u", "к": "k", "м": "m", "н": "h", "о": "o", "п": "n",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "і": "i",
    "ї": "i", "ј": "j", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "І": "I",
    "Ј": "J", "Ԍ": "G", "Ԛ": "Q", "Ԝ": "W",
    # Greek → Latin
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "τ": "t", "υ": "u", "χ": "x",
    "ι": "i", "κ": "k", "ε": "e", "μ": "u", "ϲ": "c", "ϳ": "j",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Armenian / Cherokee / fullwidth oddities that survive NFKC
    "օ": "o", "ց": "g", "ѵ": "v", "ᴀ": "a", "ᴄ": "c", "ᴇ": "e", "ɡ": "g",
    "ⅼ": "l", "ⅰ": "i", "ⲟ": "o", "ᒿ": "2",
}

_SCRIPT_CACHE: dict[str, str] = {}


def _script_of(char: str) -> str:
    """Coarse script bucket for a character, memoised."""
    cached = _SCRIPT_CACHE.get(char)
    if cached is not None:
        return cached
    try:
        name = unicodedata.name(char)
    except ValueError:
        name = ""
    script = "OTHER"
    for candidate in ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "HEBREW",
                      "ARABIC", "CHEROKEE", "DEVANAGARI", "HAN", "HIRAGANA",
                      "KATAKANA", "HANGUL", "THAI"):
        if name.startswith(candidate):
            script = candidate
            break
    _SCRIPT_CACHE[char] = script
    return script


_WORD_SPLIT_RE = re.compile(r"(\w+)", re.UNICODE)


def fold_mixed_script_words(text: str) -> tuple[str, list[str]]:
    """Fold confusables only inside words that mix scripts.

    A word written entirely in Cyrillic is legitimate Russian text and is left
    alone. A word that mixes Cyrillic and Latin letters is a homoglyph attack
    essentially every time, so it folds to its canonical Latin form.
    """
    folded_words: list[str] = []
    out_parts: list[str] = []

    for part in _WORD_SPLIT_RE.split(text):
        if not part or not part.isalnum():
            out_parts.append(part)
            continue
        scripts = {_script_of(c) for c in part if c.isalpha()}
        scripts.discard("OTHER")
        if len(scripts) > 1 and len(part) >= 2:
            replacement = "".join(CONFUSABLES.get(c, c) for c in part)
            if replacement != part:
                folded_words.append(part)
                out_parts.append(replacement)
                continue
        out_parts.append(part)

    return "".join(out_parts), folded_words


def strip_invisibles(text: str) -> tuple[str, dict[str, int]]:
    """Remove characters that render as nothing. Returns text and a tally."""
    counts: dict[str, int] = {}
    out: list[str] = []

    for char in text:
        codepoint = ord(char)
        if char in INVISIBLE_CHARS:
            counts["format_char"] = counts.get("format_char", 0) + 1
            continue
        if codepoint in TAG_RANGE:
            counts["unicode_tag"] = counts.get("unicode_tag", 0) + 1
            continue
        if codepoint in VARIATION_SUPPLEMENT:
            counts["variation_selector"] = counts.get("variation_selector", 0) + 1
            continue
        category = unicodedata.category(char)
        if category == "Cf":  # any other format char
            counts["format_char"] = counts.get("format_char", 0) + 1
            continue
        if category == "Cc" and char not in "\t\n\r":
            counts["control_char"] = counts.get("control_char", 0) + 1
            continue
        out.append(char)

    return "".join(out), counts


def decode_unicode_tags(text: str) -> str:
    """Recover the ASCII hidden inside a Unicode tag sequence.

    Used for classification only: the tags themselves never reach the agent.
    """
    return "".join(
        chr(ord(c) - 0xE0000) for c in text if ord(c) in TAG_RANGE
    )


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass
class Removal:
    """One excised region. `text` lives in memory only, never in storage."""

    kind: str
    text: str
    detail: str = ""
    depth: int = 0

    def to_audit(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "depth": self.depth,
                "chars": len(self.text)}


@dataclass
class SanitiseResult:
    text: str
    removals: list[Removal] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    folded_words: list[str] = field(default_factory=list)

    @property
    def removed_anything(self) -> bool:
        return bool(self.removals) or bool(self.folded_words)

    @property
    def hostile_removals(self) -> list[Removal]:
        """Removals whose recovered text actually scored as instruction-like."""
        return [r for r in self.removals if score_text(r.text).score >= 0.35]

    def audit_summary(self) -> dict:
        by_kind: dict[str, int] = {}
        for removal in self.removals:
            by_kind[removal.kind] = by_kind.get(removal.kind, 0) + 1
        return {
            "removals": by_kind,
            "stats": self.stats,
            "folded_words": len(self.folded_words),
        }


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

_HIDDEN_DECLARATIONS = (
    ("display", lambda v: v.strip() == "none"),
    ("visibility", lambda v: v.strip() in ("hidden", "collapse")),
    ("opacity", lambda v: _as_float(v) is not None and _as_float(v) <= 0.05),
    ("font-size", lambda v: _px(v) is not None and _px(v) <= 1.0),
    ("color", lambda v: v.strip().lower() in
        ("#fff", "#ffffff", "white", "transparent", "rgba(0,0,0,0)")),
    ("text-indent", lambda v: _px(v) is not None and _px(v) <= -500),
    ("left", lambda v: _px(v) is not None and _px(v) <= -500),
    ("top", lambda v: _px(v) is not None and _px(v) <= -500),
    ("max-height", lambda v: _px(v) is not None and _px(v) <= 0),
    ("height", lambda v: _px(v) is not None and _px(v) <= 0),
    ("width", lambda v: _px(v) is not None and _px(v) <= 0),
    ("clip", lambda v: "rect(0" in v.replace(" ", "")),
    ("clip-path", lambda v: "inset(100%" in v.replace(" ", "")),
    ("z-index", lambda v: _as_float(v) is not None and _as_float(v) <= -100),
)

_HIDDEN_UTILITY_CLASSES = {
    "sr-only", "screen-reader-only", "screen-reader-text", "visually-hidden",
    "hidden", "hide", "is-hidden", "d-none", "invisible", "offscreen",
    "off-screen", "a11y-hidden", "clip", "assistive-text",
}


def _as_float(value: str) -> float | None:
    try:
        return float(re.sub(r"[^0-9.\-]", "", value) or "nan")
    except ValueError:
        return None


def _px(value: str) -> float | None:
    value = value.strip().lower()
    match = re.match(r"^(-?\d*\.?\d+)\s*(px|pt|em|rem|%)?$", value)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "px"
    if unit in ("em", "rem"):
        number *= 16
    if unit == "pt":
        number *= 1.333
    return number


def _declarations(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in style.split(";"):
        if ":" not in chunk:
            continue
        prop, _, value = chunk.partition(":")
        out[prop.strip().lower()] = value.strip()
    return out


def style_is_hidden(style: str) -> tuple[bool, str]:
    """Does this declaration block hide its element from a human reader?"""
    declarations = _declarations(style)
    for prop, predicate in _HIDDEN_DECLARATIONS:
        value = declarations.get(prop)
        if value is not None and predicate(value):
            return True, f"{prop}:{value}"
    # White text on a white (or unset, therefore white) background.
    colour = declarations.get("color", "").strip().lower()
    background = declarations.get("background-color", declarations.get("background", "")).strip().lower()
    whites = {"#fff", "#ffffff", "white", "rgb(255,255,255)"}
    if colour in whites and (background in whites or not background):
        return True, f"color:{colour}"
    return False, ""


_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


def hidden_selectors(css: str) -> set[str]:
    """Class and id selectors that a stylesheet defines as invisible."""
    hidden: set[str] = set()
    for selector_group, body in _CSS_RULE_RE.findall(css):
        is_hidden, _ = style_is_hidden(body)
        if not is_hidden:
            continue
        for selector in selector_group.split(","):
            selector = selector.strip()
            for token in re.findall(r"[.#][A-Za-z0-9_-]+", selector):
                hidden.add(token)
    return hidden


def sanitise_html(markup: str) -> tuple[str, list[Removal]]:
    """Extract the human-visible text from HTML, banking everything else."""
    try:
        from bs4 import BeautifulSoup, Comment  # type: ignore
    except ImportError:  # pragma: no cover - exercised only without bs4
        return _sanitise_html_fallback(markup)

    removals: list[Removal] = []
    soup = BeautifulSoup(markup, "html.parser")

    # 1. Comments never render.
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        text = str(comment).strip()
        if text:
            removals.append(Removal("html_comment", text))
        comment.extract()

    # 2. Collect stylesheet-defined invisibility before dropping <style>.
    hidden_tokens: set[str] = set()
    for style_tag in soup.find_all("style"):
        hidden_tokens |= hidden_selectors(style_tag.get_text())
        style_tag.decompose()

    # 3. Non-rendering containers.
    for tag_name in ("script", "template", "noscript", "svg", "head"):
        for tag in soup.find_all(tag_name):
            if tag_name in ("script", "template", "noscript"):
                text = tag.get_text(" ", strip=True)
                if text:
                    removals.append(Removal(f"html_{tag_name}", text))
            if tag_name == "head":
                for meta in tag.find_all("meta"):
                    content = meta.get("content", "")
                    if content and meta.get("name", "").lower() not in ("viewport", "charset"):
                        removals.append(
                            Removal("html_meta", content, detail=meta.get("name", ""))
                        )
            tag.decompose()

    # 4. Elements hidden by inline style, utility class, or attribute.
    for element in soup.find_all(True):
        reason = ""
        style = element.get("style", "")
        if style:
            is_hidden, detail = style_is_hidden(style)
            if is_hidden:
                reason = f"style:{detail}"
        if not reason:
            classes = {c.lower() for c in element.get("class", [])}
            if classes & _HIDDEN_UTILITY_CLASSES:
                reason = f"class:{sorted(classes & _HIDDEN_UTILITY_CLASSES)[0]}"
            elif any(f".{c}" in hidden_tokens for c in classes):
                reason = "stylesheet:class"
            elif element.get("id") and f"#{element.get('id')}" in hidden_tokens:
                reason = "stylesheet:id"
        if not reason and element.has_attr("hidden"):
            reason = "attr:hidden"
        if not reason and element.get("aria-hidden", "").lower() == "true":
            reason = "attr:aria-hidden"

        if reason:
            text = element.get_text(" ", strip=True)
            if text:
                removals.append(Removal("hidden_element", text, detail=reason))
            element.decompose()

    # 5. Attributes that carry text but are not rendered as prose.
    for element in soup.find_all(True):
        for attribute in ("alt", "title", "aria-label", "data-instructions",
                          "data-prompt", "placeholder"):
            value = element.get(attribute)
            if value and isinstance(value, str) and value.strip():
                removals.append(
                    Removal("html_attribute", value,
                            detail=f"{element.name}[{attribute}]")
                )
                del element[attribute]
        # A link's href can itself be a beacon; keep the host, drop the payload.
        href = element.get("href") or element.get("src")
        if href and isinstance(href, str) and len(href) > 512:
            removals.append(Removal("oversized_url", href, detail=element.name))

    visible = soup.get_text("\n", strip=True)
    return visible, removals


def _sanitise_html_fallback(markup: str) -> tuple[str, list[Removal]]:
    """Regex-only path used when BeautifulSoup is unavailable."""
    removals: list[Removal] = []

    for match in re.finditer(r"<!--(.*?)-->", markup, re.S):
        removals.append(Removal("html_comment", match.group(1).strip()))
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)

    for tag in ("script", "style", "template", "noscript", "head"):
        for match in re.finditer(rf"<{tag}\b.*?</{tag}>", markup, re.S | re.I):
            removals.append(Removal(f"html_{tag}", re.sub(r"<[^>]+>", " ", match.group(0))))
        markup = re.sub(rf"<{tag}\b.*?</{tag}>", " ", markup, flags=re.S | re.I)

    for match in re.finditer(r"<(\w+)[^>]*\bstyle\s*=\s*\"([^\"]*)\"[^>]*>(.*?)</\1>",
                             markup, re.S | re.I):
        is_hidden, detail = style_is_hidden(match.group(2))
        if is_hidden:
            removals.append(
                Removal("hidden_element", re.sub(r"<[^>]+>", " ", match.group(3)).strip(),
                        detail=detail)
            )
            markup = markup.replace(match.group(0), " ")

    for match in re.finditer(r"\b(alt|title|aria-label)\s*=\s*\"([^\"]{2,})\"", markup, re.I):
        removals.append(Removal("html_attribute", match.group(2), detail=match.group(1)))

    text = html_module.unescape(re.sub(r"<[^>]+>", "\n", markup))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removals


# --------------------------------------------------------------------------
# recursive decoding
# --------------------------------------------------------------------------

_B64_RE = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]{24,}={0,2}")
_HEX_RE = re.compile(r"(?:[0-9a-fA-F]{2}[\s:]?){12,}")
_PERCENT_RE = re.compile(r"(?:%[0-9a-fA-F]{2}){4,}")
_UNICODE_ESCAPE_RE = re.compile(r"(?:\\u[0-9a-fA-F]{4}){3,}")
_HTML_ENTITY_RE = re.compile(r"(?:&#x?[0-9a-fA-F]{1,6};){3,}")
_PRINTABLE_RATIO = 0.85


def _looks_like_text(candidate: str) -> bool:
    if len(candidate) < 8:
        return False
    printable = sum(1 for c in candidate if c.isprintable() or c in "\n\t")
    if printable / len(candidate) < _PRINTABLE_RATIO:
        return False
    letters = sum(1 for c in candidate if c.isalpha())
    return letters / len(candidate) > 0.4


def _try_base64(blob: str, urlsafe: bool = False) -> str | None:
    padded = blob + "=" * (-len(blob) % 4)
    try:
        if urlsafe:
            # urlsafe_b64decode has no validate flag, so normalise first.
            raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"),
                                   validate=True)
        else:
            raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return decoded if _looks_like_text(decoded) else None


def _try_hex(blob: str) -> str | None:
    cleaned = re.sub(r"[\s:]", "", blob)
    if len(cleaned) % 2:
        cleaned = cleaned[:-1]
    try:
        decoded = bytes.fromhex(cleaned).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if _looks_like_text(decoded) else None


def _try_rot13(text: str) -> str | None:
    """ROT13 has no marker, so decode and keep it only if it becomes hostile."""
    decoded = codecs.encode(text, "rot_13")
    if score_text(decoded).score >= 0.5 and score_text(text).score < 0.35:
        return decoded
    return None


def decode_recursively(text: str, max_depth: int = 4) -> list[Removal]:
    """Peel encoded blobs, recording anything that decodes to readable text.

    Bounded depth: a decode bomb costs a fixed amount of work, not unbounded.
    """
    found: list[Removal] = []
    frontier = [(text, 0)]
    seen: set[str] = set()

    while frontier:
        current, depth = frontier.pop()
        if depth >= max_depth or len(current) > 512_000:
            continue

        candidates: list[tuple[str, str]] = []

        for match in _B64_RE.finditer(current):
            decoded = _try_base64(match.group(0))
            if decoded:
                candidates.append(("base64", decoded))
        for match in _B64URL_RE.finditer(current):
            decoded = _try_base64(match.group(0), urlsafe=True)
            if decoded:
                candidates.append(("base64url", decoded))
        for match in _HEX_RE.finditer(current):
            decoded = _try_hex(match.group(0))
            if decoded:
                candidates.append(("hex", decoded))
        for match in _PERCENT_RE.finditer(current):
            decoded = urllib.parse.unquote(match.group(0))
            if _looks_like_text(decoded):
                candidates.append(("url_encoded", decoded))
        for match in _UNICODE_ESCAPE_RE.finditer(current):
            try:
                decoded = match.group(0).encode().decode("unicode_escape")
            except UnicodeDecodeError:
                continue
            if _looks_like_text(decoded):
                candidates.append(("unicode_escape", decoded))
        for match in _HTML_ENTITY_RE.finditer(current):
            decoded = html_module.unescape(match.group(0))
            if _looks_like_text(decoded):
                candidates.append(("html_entity", decoded))

        rotated = _try_rot13(current)
        if rotated:
            candidates.append(("rot13", rotated))

        for kind, decoded in candidates:
            key = f"{kind}:{decoded[:200]}"
            if key in seen:
                continue
            seen.add(key)
            found.append(Removal(f"decoded_{kind}", decoded, depth=depth + 1))
            frontier.append((decoded, depth + 1))

    return found


# --------------------------------------------------------------------------
# binary formats
# --------------------------------------------------------------------------


def extract_pdf_text(data: bytes) -> tuple[str, list[Removal]]:
    """Visible page text forward; metadata and annotations banked."""
    removals: list[Removal] = []
    try:
        import io

        from pypdf import PdfReader  # type: ignore
    except ImportError:  # pragma: no cover
        return "", [Removal("pdf_unavailable", "", detail="pypdf not installed")]

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pragma: no cover - malformed input
        return "", [Removal("pdf_unreadable", "", detail=str(exc)[:120])]

    for key, value in (reader.metadata or {}).items():
        if isinstance(value, str) and value.strip():
            removals.append(Removal("pdf_metadata", value, detail=str(key)))

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # pragma: no cover
            continue
        for annotation in page.get("/Annots", []) or []:
            try:
                obj = annotation.get_object()
            except Exception:  # pragma: no cover
                continue
            for field_name in ("/Contents", "/T", "/Subj"):
                value = obj.get(field_name)
                if isinstance(value, str) and value.strip():
                    removals.append(Removal("pdf_annotation", value, detail=field_name))

    return "\n".join(pages), removals


def extract_image_metadata(data: bytes) -> list[Removal]:
    """EXIF and friends: text an agent reads but a viewer never shows."""
    removals: list[Removal] = []
    try:
        import io

        from PIL import ExifTags, Image  # type: ignore
    except ImportError:  # pragma: no cover
        return [Removal("exif_unavailable", "", detail="Pillow not installed")]

    try:
        image = Image.open(io.BytesIO(data))
        exif = image.getexif()
    except Exception as exc:  # pragma: no cover
        return [Removal("image_unreadable", "", detail=str(exc)[:120])]

    for tag_id, value in (exif or {}).items():
        if not isinstance(value, (str, bytes)):
            continue
        text = value.decode("utf-8", "ignore") if isinstance(value, bytes) else value
        if text.strip():
            name = ExifTags.TAGS.get(tag_id, str(tag_id))
            removals.append(Removal("exif", text, detail=name))

    for key, value in (getattr(image, "info", {}) or {}).items():
        if isinstance(value, str) and value.strip() and key.lower() not in ("dpi",):
            removals.append(Removal("image_info", value, detail=str(key)))

    return removals


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def sanitise(
    content: str | bytes,
    *,
    content_type: str = "text/plain",
    max_decode_depth: int = 4,
) -> SanitiseResult:
    """Run the full L1 pass over one piece of untrusted content."""
    removals: list[Removal] = []
    stats: dict[str, int] = {}

    if isinstance(content, bytes):
        if "pdf" in content_type:
            text, pdf_removals = extract_pdf_text(content)
            removals.extend(pdf_removals)
        elif "image" in content_type:
            text = ""
            removals.extend(extract_image_metadata(content))
        else:
            text = content.decode("utf-8", "replace")
    else:
        text = content

    if "html" in content_type or re.search(r"<(?:html|body|div|span|p|script|style|img)\b",
                                           text, re.I):
        text, html_removals = sanitise_html(text)
        removals.extend(html_removals)

    # Recover text hidden in Unicode tag characters before they are stripped.
    tag_payload = decode_unicode_tags(text)
    if tag_payload.strip():
        removals.append(Removal("unicode_tag_payload", tag_payload))

    text = unicodedata.normalize("NFKC", text)
    text, invisible_counts = strip_invisibles(text)
    stats.update(invisible_counts)

    text, folded = fold_mixed_script_words(text)

    # Decode over the union of visible text and everything banked so far, so a
    # payload that is both hidden *and* encoded is still recovered.
    decode_source = "\n".join([text] + [r.text for r in removals])
    removals.extend(decode_recursively(decode_source, max_depth=max_decode_depth))

    # Collapse the whitespace that stripping leaves behind.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    stats["removals"] = len(removals)
    stats["output_chars"] = len(text)

    return SanitiseResult(text=text, removals=removals, stats=stats, folded_words=folded)
