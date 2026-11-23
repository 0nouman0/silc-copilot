"""HTML from the CMS fields to readable Markdown-ish text.

Real tables (fee structures, eligibility grids) become Markdown tables so the UI renders them as tables; lists become
"- " bullets; everything else becomes plain paragraphs. Nothing is added or reworded.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.depth = 0                      # table nesting (nested tables are flattened into their cell)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def _w(self, s: str):
        (self.cell if self.cell is not None else self.out).append(s)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "table":
            if self.depth == 0:
                self.out.append("\n")
                self.rows = []
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("td", "th"):
            self.cell = []
        elif tag == "li":
            self._w(" " if self.cell is not None else "\n- ")
        elif tag in _BLOCK:
            self._w(" " if self.cell is not None else "\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self._emit_table()
        elif self.depth == 1 and tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(_clean("".join(self.cell)))
            self.cell = None
        elif self.depth == 1 and tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag in _BLOCK or tag == "li":
            self._w(" " if self.cell is not None else "\n")

    def handle_data(self, data):
        if not self.skip:
            self._w(data)

    def _emit_table(self):
        rows = [r for r in self.rows if any(r)]
        self.rows = []
        if not rows:
            return
        width = max(len(r) for r in rows)
        if width == 1:                      # layout table: just text
            self.out.append("\n" + "\n".join(r[0] for r in rows) + "\n")
            return
        esc = lambda c: c.replace("|", "\\|")
        lines = ["| " + " | ".join(esc(c) for c in r + [""] * (width - len(r))) + " |" for r in rows]
        lines.insert(1, "|" + "---|" * width)
        self.out.append("\n" + "\n".join(lines) + "\n")


_MOJIBAKE = re.compile(r"(?:[ÂÃâ][\u0080-¿€‚-›™ŒœŠšŸŽžˆ˜ƒ]+)+")


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 that was stored as if it were Windows-1252 (e.g. 'â€™' -> '’', 'Â ' -> ' '). Only touches sequences that
    decode cleanly; anything else is left exactly as stored."""
    def one(m: re.Match) -> str:
        try:
            return m.group(0).encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)
    text = _MOJIBAKE.sub(one, text)
    return re.sub(r"Â(?![A-Za-z])", "", text)  # a lone leftover byte from a double-encoded non-breaking space


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    p = _P()
    try:
        p.feed(html)
        p.close()
        text = "".join(p.out)
    except Exception:  # noqa: BLE001  malformed CMS markup: fall back to a plain tag strip
        text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text).replace("\xa0", " ")
    text = fix_mojibake(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"(?m)^- *\n(?=\S)", "- ", text)     # a list item whose text sits in a nested paragraph: keep the bullet with its text
    return text.strip()


def tidy(text: str | None) -> str:
    """Markdown paragraphs for display: bullet and table lines stay together, every other line is its own paragraph.
    Pure layout; the wording is untouched."""
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    out: list[str] = []
    prev_kind = None
    for ln in lines:
        kind = "table" if ln.startswith("|") else "bullet" if ln.startswith("- ") else "para"
        if out and kind == prev_kind and kind != "para":
            out[-1] += "\n" + ln
        else:
            out.append(ln)
        prev_kind = kind
    return "\n\n".join(out)
