"""Readable text from scraped pages.

Scraped HTML gives one block per line, includes button labels ("Apply Now") and splits
labels from their values ("Last date to apply" / "29" / "November 2026"). `prose` repairs
that into markdown paragraphs that the UI renders cleanly. It never invents content: it
only removes call-to-action labels and re-joins fragments.
"""
from __future__ import annotations

import re

_CTA_WORDS = (r"apply now|apply online|get more information|read more|know more|learn more|click here|view more|view details|explore(?: programme)?|"
              r"enquire now|register now|download|more details|submit|view brochure|know more about|visit website")
CTA_LINE = re.compile(rf"^(?:(?:{_CTA_WORDS})[\s.,|/>»-]*)+$", re.I)
_DATE = re.compile(r"\d{1,2} [A-Z][a-z]+ \d{4}")


def prose(text: str | None) -> str:
    """Markdown paragraphs: one per scraped line, tables / lists kept together, CTA labels dropped, split dates re-joined."""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    cleaned: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln or CTA_LINE.match(ln):
            i += 1
        elif re.fullmatch(r"\d{1,2}", ln) and i + 1 < len(lines) and re.fullmatch(r"[A-Z][a-z]+ \d{4}", lines[i + 1]):
            cleaned.append(f"{ln} {lines[i + 1]}")  # "29" + "November 2026"
            i += 2
        else:
            cleaned.append(ln)
            i += 1
    # a short label directly followed by a date becomes "Label: date"
    joined: list[str] = []
    for ln in cleaned:
        if joined and _DATE.fullmatch(ln) and len(joined[-1]) <= 45 and not re.search(r"[.:;!?|]$", joined[-1]) and not joined[-1].startswith(("|", "- ", "#")):
            joined[-1] = f"{joined[-1]}: {ln}"
        else:
            joined.append(ln)
    blocks, cur, kind = [], [], None
    prev = None
    for ln in joined:
        if ln == prev and not ln.startswith("|"):
            continue  # an immediately repeated line
        prev = ln
        k = ("table" if ln.startswith("|") else "bullet" if re.match(r"^[-*•]\s", ln) else "ordered" if re.match(r"^\d+[.)]\s", ln)
             else "heading" if ln.startswith("#") else "para")
        if k != kind or k in ("para", "heading"):
            if cur:
                blocks.append("\n".join(cur))
            cur = []
        kind = k
        cur.append(ln)
    if cur:
        blocks.append("\n".join(cur))
    return "\n\n".join(blocks)
