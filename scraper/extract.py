"""HTML -> clean markdown-ish text, plus link / PDF discovery."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup, NavigableString

INLINE = ["a", "b", "strong", "em", "i", "u", "span", "small", "sup", "sub", "font", "mark", "label", "abbr", "cite", "code", "big"]
DROP = ["script", "style", "noscript", "svg", "iframe", "form", "button", "select", "input", "textarea", "canvas", "template", "head"]
PDF_RE = re.compile(r"\.pdf(\?|#|$)", re.I)


@dataclass
class Page:
    title: str = ""
    description: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    pdfs: list[str] = field(default_factory=list)


def _clean(s: str) -> str:
    s = s.replace("\xa0", " ").replace("​", "")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def _table_md(t) -> str:
    rows = []
    for tr in t.find_all("tr"):
        cells = [_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    esc = lambda c: c.replace("|", "/")
    out = ["| " + " | ".join(esc(c) for c in rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(out)


def extract(html: str, base_url: str) -> Page:
    soup = BeautifulSoup(html, "lxml")
    pg = Page()
    if soup.title and soup.title.string:
        pg.title = _clean(soup.title.string)
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content") and md["content"].strip() != pg.title:
        pg.description = _clean(md["content"])

    # discover links / pdfs BEFORE we mutate the tree
    seen_l, seen_p = set(), set()
    for tag, attr in (("a", "href"), ("link", "href"), ("iframe", "src"), ("embed", "src"), ("object", "data"), ("source", "src")):
        for el in soup.find_all(tag):
            v = (el.get(attr) or "").strip()
            if not v or v.startswith(("mailto:", "tel:", "javascript:", "#", "data:")) or "${" in v:
                continue
            u = urldefrag(urljoin(base_url, v))[0]
            if PDF_RE.search(u):
                if u not in seen_p:
                    seen_p.add(u); pg.pdfs.append(u)
            elif tag == "a" and u not in seen_l:
                seen_l.add(u); pg.links.append(u)

    for t in soup(DROP):
        t.decompose()
    for t in soup.find_all(["nav", "footer", "aside"]):
        t.decompose()
    for t in soup.find_all(True, attrs={"aria-hidden": "true"}):
        if t.name not in ("html", "body"):
            t.decompose()

    for t in soup.find_all("table"):
        t.replace_with(NavigableString("\n" + _table_md(t) + "\n"))
    for lvl in range(1, 7):
        for h in soup.find_all(f"h{lvl}"):
            h.insert_before(NavigableString("\n" + "#" * min(lvl + 1, 4) + " "))
            h.append(NavigableString("\n"))
    for li in soup.find_all("li"):
        li.insert_before(NavigableString("\n- "))
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for t in soup.find_all(INLINE):
        t.unwrap()
    soup.smooth()

    body = soup.body or soup
    lines = []
    for raw in body.get_text("\n").split("\n"):
        ln = _clean(raw)
        if ln and ln not in ("-", "#", "##", "###", "####"):
            lines.append(ln)
    pg.text = "\n".join(lines)
    return pg
