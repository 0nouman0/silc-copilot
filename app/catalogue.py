"""The programme catalogue, read from the CHRIST database through the `CatalogueSource`.

Holds one lightweight row per programme listing (a programme offered on several campuses is several rows) and
resolves free-text mentions ("mba fees", "BCom Hons") to those rows, asking for clarification when a mention is
genuinely ambiguous. Nothing is invented: it only knows what the source returns. The list is cached for a few
minutes and rebuilt when the source changes; if the source is down `SourceUnavailable` propagates.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .sources import SourceUnavailable, get_source

TTL_S = 600
STOP = {"the", "a", "an", "of", "to", "is", "are", "and", "or", "in", "on", "for", "i", "my", "do", "does", "what", "how", "can", "it", "me",
        "with", "if", "be", "this", "that", "at", "by", "from", "will", "there", "you", "your", "we", "our", "any", "about", "which", "who",
        "when", "where", "please", "tell", "show", "give", "want", "need", "get", "have", "has", "am", "was", "so", "as", "its"}
ABBR = {
    "mba": "master business administration", "bba": "bachelor business administration", "bcom": "bachelor commerce",
    "mcom": "master commerce", "bsc": "bachelor science", "msc": "master science", "ba": "bachelor arts", "ma": "master arts",
    "bca": "bachelor computer applications", "mca": "master computer applications", "btech": "bachelor technology",
    "mtech": "master technology", "be": "bachelor engineering", "llb": "bachelor laws", "llm": "master laws",
    "bed": "bachelor education", "med": "master education", "bsw": "bachelor social work", "msw": "master social work",
    "bhm": "bachelor hotel management", "bdes": "bachelor design", "barch": "bachelor architecture", "phd": "doctor philosophy",
    "bfa": "bachelor fine arts", "mfa": "master fine arts", "bpa": "bachelor performing arts", "mpa": "master performing arts",
}


def stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith("es") and t[-3] in "sxz":
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", text.lower())  # B.Com -> bcom
    text = text.replace(".", "")
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in STOP and len(t) > 1]


ABBR_TOKENS = {k: set(tokenize(v)) for k, v in ABBR.items()}
# words people use when asking ABOUT a programme; they must not pick one ("application dates" must not match "Computer Applications")
QUESTION_WORDS = {stem(w) for w in ("application", "applications", "apply", "applying", "admission", "date", "deadline", "fee", "eligibility", "eligible",
                                    "process", "selection", "curriculum", "syllabus", "last", "open", "close", "status", "detail", "information", "info",
                                    "scholarship", "placement", "hostel", "campus", "criteria", "requirement", "available", "offer", "programme", "course")}


def window_status(open_: str | None, close: str | None) -> str:
    """open / upcoming / closed relative to today, from the dates stored on the programme record."""
    today = date.today().isoformat()
    if close:
        return "closed" if close < today else "upcoming" if (open_ and open_ > today) else "open"
    # no closing date on the record: an old opening date alone does not make the window "open"
    return "upcoming" if (open_ and open_ > today) else "unknown"


def level_key(label: str | None) -> str:
    s = (label or "").lower()
    return "undergraduate" if s.startswith("undergrad") else "postgraduate" if s.startswith("postgrad") else "doctoral" if s.startswith("doctoral") else "other"


@dataclass(frozen=True)
class Programme:
    id: int
    name: str
    level: str                      # undergraduate | postgraduate | doctoral | other
    level_label: str | None = None  # the category name as stored
    campus: str | None = None
    department: str | None = None
    duration_years: Any = None
    term_type: str | None = None
    approved_intake: Any = None
    application_open: str | None = None
    application_close: str | None = None
    record_last_modified: str | None = None

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> "Programme":
        return cls(id=int(r["course_id"]), name=r["name"], level=level_key(r.get("level")), level_label=r.get("level"), campus=r.get("campus"),
                   department=r.get("department"), duration_years=r.get("duration_years"), term_type=r.get("term_type"),
                   approved_intake=r.get("approved_intake"), application_open=r.get("application_open"),
                   application_close=r.get("application_close"), record_last_modified=r.get("record_last_modified"))

    @property
    def status(self) -> str:
        return window_status(self.application_open, self.application_close)


@dataclass
class Resolution:
    best: Programme | None
    candidates: list[Programme]
    distinct: int = 0  # distinct programmes among candidates (campus listings / "(MBA)"-style naming variants count once)

    @property
    def ambiguous(self) -> bool:
        return self.distinct > 1


class Catalogue:
    def __init__(self, rows: list[dict[str, Any]]):
        self.programmes = [Programme.from_row(r) for r in rows]
        self.by_id = {p.id: p for p in self.programmes}
        self.name_tokens: dict[int, set[str]] = {}
        df: Counter = Counter()
        for p in self.programmes:
            toks = set(tokenize(p.name))
            self.name_tokens[p.id] = toks
            df.update(toks)
        n = max(len(self.programmes), 1)
        self.name_idf = {t: math.log(1 + n / c) for t, c in df.items()}
        # programme identity ignoring a name's own abbreviation: "Master of Business Administration" == "... (MBA)"
        self.name_key = {pid: frozenset(t for t in nt if not (t in ABBR_TOKENS and ABBR_TOKENS[t] <= nt)) for pid, nt in self.name_tokens.items()}

    # ------------------------------------------------------------------ browse
    def campuses(self) -> list[str]:
        return sorted({p.campus for p in self.programmes if p.campus})

    def by_level(self) -> Counter:
        return Counter(p.level for p in self.programmes)

    def windows(self) -> list[tuple]:
        """(level, campus, open, close, count) for every distinct application window on the records."""
        w = Counter((p.level, p.campus, p.application_open, p.application_close) for p in self.programmes if p.application_open or p.application_close)
        return [(*k, n) for k, n in sorted(w.items(), key=lambda x: str(x[0]))]

    def stats(self) -> dict:
        lv = self.by_level()
        return {"source": "CHRIST programme database", "programmes": len(self.programmes), "undergraduate": lv["undergraduate"],
                "postgraduate": lv["postgraduate"], "doctoral": lv["doctoral"], "other": lv["other"], "campuses": len(self.campuses())}

    # ------------------------------------------------------------------ name matching
    def _units(self, text: str) -> list[tuple]:
        """What the user typed, as (token, tokens it stands for, idf mass, is_abbreviation). Words that appear in no
        programme name ("fees", "apply") are noise and are dropped."""
        units = []
        for t in dict.fromkeys(tokenize(text)):
            if t in QUESTION_WORDS:
                continue
            if t in ABBR:
                toks = {t} | ABBR_TOKENS[t]
                mass = max((self.name_idf.get(x, 0.0) for x in toks), default=0.0)
                if mass:
                    units.append((t, toks, mass, True))
            elif t in self.name_idf:
                units.append((t, {t}, self.name_idf[t], False))
        return units

    def _satisfies(self, pid: int, unit: tuple) -> bool:
        t, _toks, _mass, is_abbr = unit
        nt = self.name_tokens[pid]
        return (t in nt or ABBR_TOKENS[t] <= nt) if is_abbr else (t in nt)

    def search(self, query: str | None = None, level: str | None = None, campus: str | None = None, limit: int = 50) -> list[Programme]:
        """Programmes matching ALL typed words (abbreviations count by literal or full phrase); relax to ANY only if none do."""
        pool = [p for p in self.programmes if (not level or p.level == level) and (not campus or (p.campus and campus.lower() in p.campus.lower()))]
        if query and tokenize(query):
            units = self._units(query)
            if not units:
                return []
            strict = [p for p in pool if all(self._satisfies(p.id, u) for u in units)]
            pool = strict or [p for p in pool if any(self._satisfies(p.id, u) for u in units)]
            pool.sort(key=lambda p: (len(self.name_tokens[p.id]), p.name))
        return pool[:limit]

    def resolve(self, text: str) -> Resolution:
        """Map a free-text mention to catalogue programme(s). Ambiguity is reported, never guessed.

        Scoring is an F1 of precision (how much of what the user typed the name accounts for) and recall (how much
        of the name the user typed), so a short unrelated name cannot win on coverage alone. Typed abbreviations
        (MBA, BBA, B.Com) must be satisfied literally or by their full phrase. An exact name match wins outright.
        """
        typed = list(dict.fromkeys(tokenize(text)))
        units = self._units(text)
        if not units:
            return Resolution(None, [])
        typed_set, qmass = set(typed), sum(u[2] for u in units)
        rows = []
        for p in self.programmes:
            nt = self.name_tokens[p.id]
            if not nt:
                continue
            sat, matched, abbr_ok = 0.0, set(), True
            for t, toks, mass, is_abbr in units:
                ok = (t in nt or ABBR_TOKENS[t] <= nt) if is_abbr else (t in nt)
                if ok:
                    sat += mass
                    matched |= toks & nt
                elif is_abbr:
                    abbr_ok = False
            for a in nt:  # the name's own abbreviation is covered when the user typed the full phrase
                if a in ABBR_TOKENS and ABBR_TOKENS[a] <= typed_set:
                    matched.add(a)
            if not matched:
                continue
            prec = sat / qmass
            rec = sum(self.name_idf[x] for x in matched) / sum(self.name_idf[x] for x in nt)
            if prec > 0 and rec > 0:
                rows.append((round(2 * prec * rec / (prec + rec), 3), prec, rec, abbr_ok, p))
        if not rows:
            return Resolution(None, [])
        if any(r[3] for r in rows):  # typed abbreviations are strict whenever some programme satisfies them all
            rows = [r for r in rows if r[3]]
        rows.sort(key=lambda r: r[0], reverse=True)

        def done(cands: list[Programme]) -> Resolution:
            cands = cands[:8]
            return Resolution(cands[0], cands, len({self.name_key[p.id] for p in cands}))

        exact = [r[4] for r in rows if r[1] >= 0.999 and r[2] >= 0.999]
        if exact:
            return done(exact)
        comps = [r[4] for r in rows if r[1] >= 0.999]  # everything typed is satisfied: list them, don't pick one
        if comps:
            return done(comps)
        best = rows[0][0]
        if best < 0.5:
            return Resolution(None, [])
        return done([r[4] for r in rows if r[0] >= best - 0.12])


# ---------------------------------------------------------------------- cached access
_cache: dict[str, Any] = {"src": None, "at": 0.0, "cat": None}


def get_catalogue() -> Catalogue:
    src = get_source()
    if src is None:
        raise SourceUnavailable("no catalogue source configured")
    c = _cache
    if c["cat"] is None or c["src"] is not src or time.monotonic() - c["at"] > TTL_S:
        cat = Catalogue(src.list_catalogue())
        c.update(src=src, at=time.monotonic(), cat=cat)
    return c["cat"]


def reset_catalogue() -> None:
    _cache.update(src=None, at=0.0, cat=None)
