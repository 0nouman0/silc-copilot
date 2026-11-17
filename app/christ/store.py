"""Corpus built from the scraped CHRIST website (see scraper/run.py).

Loads data/christ/{programmes.json,pages.jsonl,meta.json}, builds a BM25 index over
page chunks + programme sections, and resolves free-text programme mentions
("mba fees", "BCom Hons") to catalogue entries — asking for clarification when
a mention is genuinely ambiguous. Nothing here invents data: whatever the site
did not publish simply is not in the corpus.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import Programme, SourceRef, Tab

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "christ"
MIN_SCORE = 3.0  # BM25 below this => treated as "no relevant information"
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


class DataNotReady(Exception):
    """The scraped data files do not exist yet."""


def stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith("es") and t[-3] in "sxz":
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", text.lower())  # B.Com -> bcom (handles b.c.o.m patterns lightly)
    text = text.replace(".", "")
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in STOP and len(t) > 1]


ABBR_TOKENS = {k: set(tokenize(v)) for k, v in ABBR.items()}


@dataclass
class Chunk:
    text: str
    heading: str
    title: str
    url: str
    kind: str  # page | pdf | external | programme
    category: str
    scope: str  # global | programme
    programme_id: str | None = None
    label: str | None = None
    tf: Counter = field(default_factory=Counter, repr=False)
    length: int = 0


@dataclass
class Hit:
    chunk: Chunk
    score: float

    def source(self) -> SourceRef:
        c = self.chunk
        return SourceRef(doc_id=c.url, title=c.title if not c.heading or c.heading == c.title else f"{c.title} — {c.heading}",
                         url=c.url, external=c.kind == "external")


@dataclass
class Resolution:
    best: Programme | None
    candidates: list[Programme]

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _split(text: str, limit: int = 1100) -> list[tuple[str, str]]:
    """Split into (heading, body) pieces on markdown headings, then by size at line boundaries."""
    out, heading, buf = [], "", []

    def flush():
        body = "\n".join(buf).strip()
        if not body:
            return
        piece = []
        size = 0
        for ln in body.split("\n"):
            if size + len(ln) > limit and piece:
                out.append((heading, "\n".join(piece)))
                piece, size = [], 0
            piece.append(ln)
            size += len(ln) + 1
        if piece:
            out.append((heading, "\n".join(piece)))

    for ln in text.split("\n"):
        m = re.match(r"^#{2,4}\s+(.*)", ln)
        if m:
            flush()
            heading, buf = m.group(1).strip(), []
        else:
            buf.append(ln)
    flush()
    return out


class Corpus:
    def __init__(self, directory: Path):
        pj, pg, mj = directory / "programmes.json", directory / "pages.jsonl", directory / "meta.json"
        if not pj.exists() or not pg.exists():
            raise DataNotReady(f"Scraped data not found in {directory}. Run: python -m scraper.run")
        self.programmes = [Programme.model_validate(p) for p in json.loads(pj.read_text("utf-8"))]
        self.by_id = {p.id: p for p in self.programmes}
        self.meta = json.loads(mj.read_text("utf-8")) if mj.exists() else {}
        self.pages = [json.loads(ln) for ln in pg.read_text("utf-8").splitlines() if ln.strip()]
        self.chunks: list[Chunk] = []
        self._build_chunks()
        self._build_index()
        self._build_name_index()

    # ------------------------------------------------------------------ build
    def _add(self, **kw):
        self.chunks.append(Chunk(**kw))

    def _build_chunks(self):
        for pg in self.pages:
            for heading, body in _split(pg["text"]):
                if len(body) >= 40:
                    self._add(text=body, heading=heading or pg["title"], title=pg["title"] or pg["url"], url=pg["url"], kind=pg["kind"],
                              category=pg.get("category", "general"), scope="global", label=pg.get("source_label"))
        seen_generic: set[str] = set()
        for p in self.programmes:
            sections = [("Eligibility", p.eligibility, "admissions"), ("Fees", p.fees, "fees"), ("Overview", p.overview, "programmes")] + \
                       [(t.title, t.text, "admissions" if re.search(r"admission|application|selection|categor|queries", t.title, re.I) else "programmes")
                        for t in p.tabs]
            for label, body, cat in sections:
                if not body:
                    continue
                digest = hashlib.sha1(re.sub(r"\s+", " ", body).encode()).hexdigest()
                generic = label not in ("Eligibility", "Fees", "Overview")
                for heading, piece in _split(body):
                    self._add(text=piece, heading=label if not heading else f"{label}: {heading}", title=p.name, url=p.url, kind="programme",
                              category=cat, scope="programme", programme_id=p.id, label=label)
                # tabs that are identical across many programmes (process, FAQs): index ONE global copy
                if generic and digest not in seen_generic:
                    seen_generic.add(digest)
                    for heading, piece in _split(body):
                        self._add(text=piece, heading=label, title=f"CHRIST programme information — {label}", url=p.url, kind="page",
                                  category=cat, scope="global")

    def _build_index(self):
        self.index: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(self.chunks):
            toks = tokenize(c.text) + tokenize(c.heading) * 3 + tokenize(c.title) * 3
            c.tf, c.length = Counter(toks), len(toks)
            for t in c.tf:
                self.index[t].append(i)
        self.avgdl = (sum(c.length for c in self.chunks) / max(len(self.chunks), 1)) or 1.0

    def _build_name_index(self):
        self.name_tokens: dict[str, set[str]] = {}
        df: Counter = Counter()
        for p in self.programmes:
            toks = set(tokenize(p.name))
            self.name_tokens[p.id] = toks
            df.update(toks)
        n = max(len(self.programmes), 1)
        self.name_idf = {t: math.log(1 + n / c) for t, c in df.items()}

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, k: int = 4, categories: set[str] | None = None, kinds: set[str] | None = None,
               programme_id: str | None = None, min_score: float = MIN_SCORE) -> list[Hit]:
        q = set(tokenize(query))
        if not q:
            return []
        n = len(self.chunks)
        scores: dict[int, float] = defaultdict(float)
        for t in q:
            post = self.index.get(t)
            if not post:
                continue
            idf = math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for i in post:
                c = self.chunks[i]
                tf = c.tf[t]
                scores[i] += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * c.length / self.avgdl))
        hits = []
        for i, s in scores.items():
            c = self.chunks[i]
            if programme_id is not None:
                if c.programme_id != programme_id:
                    continue
            elif c.scope == "programme":
                continue
            if categories and c.category not in categories:
                continue
            if kinds and c.kind not in kinds:
                continue
            if s >= min_score:
                hits.append(Hit(c, round(s, 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        # at most 2 chunks per page so one long document cannot crowd out others
        out, per = [], Counter()
        for h in hits:
            if per[h.chunk.url] < 2:
                out.append(h)
                per[h.chunk.url] += 1
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------------ programmes
    def search_programmes(self, query: str | None = None, level: str | None = None, campus: str | None = None, limit: int = 50) -> list[Programme]:
        qt = set(tokenize(query or ""))
        out = []
        for p in self.programmes:
            if level and p.level != level:
                continue
            if campus and (not p.campus or campus.lower() not in p.campus.lower()):
                continue
            if qt:
                nt = self.name_tokens[p.id] | set(tokenize(p.campus or ""))
                exp = set()
                for t in list(qt):
                    exp.update(tokenize(ABBR.get(t, "")))
                if not (qt | exp) & nt:
                    continue
            out.append(p)
        if qt:
            def rel(p):
                nt = self.name_tokens[p.id]
                return -sum(self.name_idf.get(t, 0) for t in qt & nt)
            out.sort(key=rel)
        return out[:limit]

    def resolve(self, text: str) -> Resolution:
        """Map a free-text mention to catalogue programme(s). Ambiguity is reported, never guessed."""
        raw = tokenize(text)
        q = set(raw)
        for t in raw:
            q.update(tokenize(ABBR.get(t, "")))
        if not q:
            return Resolution(None, [])
        scored = []
        for p in self.programmes:
            nt = self.name_tokens[p.id]
            if not nt:
                continue
            matched = q & nt
            # a name's own abbreviation token (e.g. 'mba') counts as matched when the user typed the full phrase
            for t in nt:
                full = ABBR_TOKENS.get(t)
                if full and full <= q:
                    matched = matched | {t}
            if not matched:
                continue
            mass = sum(self.name_idf[t] for t in matched)
            total = sum(self.name_idf[t] for t in nt)
            # a bare abbreviation like "mba" must literally appear in the user's words OR be expanded fully
            scored.append((mass / total, mass, p))
        if not scored:
            return Resolution(None, [])
        scored = [(round(sc, 3), m, p) for sc, m, p in scored]  # float noise must not decide ties
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best, best_mass = scored[0][0], scored[0][1]
        if best < 0.62:
            return Resolution(None, [])
        cands = [p for s, m, p in scored if s >= best - 0.02 and m >= best_mass * 0.97]
        return Resolution(cands[0], cands[:8])

    def source_for(self, p: Programme, label: str = "") -> SourceRef:
        return SourceRef(doc_id=p.url, title=f"{p.name}{' — ' + label if label else ''}", url=p.url)

    def stats(self) -> dict:
        return {"scraped_at": self.meta.get("scraped_at"), "programmes": len(self.programmes), "pages": len(self.pages),
                "site": self.meta.get("source_site"), "external_sources": self.meta.get("external_sources", []),
                "pages_by_kind": self.meta.get("pages_by_kind", {})}


_corpus: Corpus | None = None


def data_dir() -> Path:
    return Path(os.getenv("CHRIST_DATA_DIR") or DEFAULT_DIR)


def get_corpus() -> Corpus:
    global _corpus
    if _corpus is None:
        _corpus = Corpus(data_dir())
    return _corpus


def reset_corpus() -> None:
    global _corpus
    _corpus = None
