"""HTML to readable text for CMS fields (table cells become 'a | b | c' rows)."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "tr"}


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK or tag == "li":
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


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
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"^ \| ", "", text, flags=re.M)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()
