"""Text cleaning for CMS fields: HTML to text (tables kept), encoding repair, double-escaped HTML, embedded images dropped."""
from __future__ import annotations

import hashlib
import re
from html import unescape

from ..sources.text import html_to_text
from .models import Table

_DATA_IMG = re.compile(r"<img[^>]+src\s*=\s*[\"']data:image[^>]*>|data:image/[a-z+]+;base64,[A-Za-z0-9+/=\s]+", re.I)
_ESCAPED_TAG = re.compile(r"&lt;\s*/?\s*[a-z][a-z0-9]*[\s&>/]", re.I)
_TABLE_LINE = re.compile(r"^\|.*\|\s*$")
_SEP = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")


def clean_html(raw: str | None) -> str:
    """CMS HTML -> readable text. Tables survive as Markdown tables; images and base64 blobs are dropped."""
    if not raw:
        return ""
    raw = _DATA_IMG.sub(" ", raw)
    if _ESCAPED_TAG.search(raw):          # double-escaped markup ("&lt;p&gt;...")
        raw = unescape(raw)
        raw = _DATA_IMG.sub(" ", raw)
    return html_to_text(raw)


def parse_tables(text: str) -> tuple[Table, ...]:
    """Structured tables from the Markdown tables that `clean_html` emits."""
    tables: list[Table] = []
    block: list[str] = []
    for ln in text.split("\n") + [""]:
        if _TABLE_LINE.match(ln.strip()):
            block.append(ln.strip())
            continue
        if len(block) >= 2:
            cells = [[c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", b.strip()[1:-1])] for b in block if not _SEP.match(b)]
            if cells:
                tables.append(Table(header=tuple(cells[0]), rows=tuple(tuple(r) for r in cells[1:])))
        block = []
    return tuple(tables)


_NOT_ROLE_WORDS = re.compile(
    r"\b(candidates?|eligib\w*|minimum|marks|applicants?|preference|admission|qualif\w*|percentage|entrance|who|should|must|willing|passion|requirements?|"
    r"basic understanding|may|which|that|can|will|ensur\w*|including|potential|opportunit\w*|advancement|promotions?|growth|informed|trends|knowledge|skills?|"
    r"support|provision|incubation|interested|to)\b", re.I)


def looks_like_role(title: str | None) -> bool:
    """A career path is a short noun phrase ('Business Analyst'), not a sentence about benefits or requirements."""
    t = one_line(title)
    if not t or len(t) > 70 or _NOT_ROLE_WORDS.search(t):
        return False
    first = t.split(" ", 1)[0].lower()
    return not (first.endswith("ing") and len(first) > 4)


def one_line(v: str | None) -> str:
    return re.sub(r"\s+", " ", v or "").strip()


def content_hash(*parts: str | None) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((p or "").encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def ints(v: object) -> list[int]:
    """All integers in a CMS list field (ids separated by backticks, commas...)."""
    return [int(x) for x in re.findall(r"\d+", str(v or ""))]


def split_list(v: str | None) -> tuple[str, ...]:
    """Backtick-separated CMS list -> cleaned items."""
    return tuple(x for x in (one_line(clean_html(p)) for p in (v or "").split("`")) if x)
