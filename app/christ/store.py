"""Corpus built from the scraped CHRIST website (see scraper/run.py).

Loads data/christ/{programmes.json,pages.jsonl,meta.json}, builds a BM25 index over
page chunks + programme sections, and resolves free-text programme mentions
("mba fees", "BCom Hons") to catalogue entries — asking for clarification when
a mention is genuinely ambiguous. Nothing here invents data: whatever the site
did not publish simply is not in the corpus.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
from urllib.parse import urlsplit
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import Programme, SourceRef, Tab

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = PROJECT_ROOT / "data" / "christ"          # live scrape output (local, gitignored)
DEPLOY_DIR = PROJECT_ROOT / "deploy_data" / "christ"    # compact bundle shipped with deployments (python scrape.py bundle)
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


GENERIC_TITLE = re.compile(r"^\s*(CHRIST( UNIVERSITY)?( \(Deemed to be University\))?|CHRIST CUx|Home|Index)\s*$", re.I)
SLUG_PREFIX_SKIP = {"center", "centre", "c", "departments", "admission", "view-pdf", "main-campus"}
SLUG_TOO_GENERIC = {"contact", "contact-us", "contact-us.php", "home", "index", "about", "about-us", "overview", "introduction"}


def humanize(slug: str) -> str:
    s = re.sub(r"\.(php|html?)$", "", slug)
    s = re.sub(r"[-_]+", " ", s).strip()
    return s[:1].upper() + s[1:] if s else s


def page_title(title: str, url: str) -> str:
    """Most CHRIST pages share the HTML title 'CHRIST UNIVERSITY'; derive a meaningful one from the URL path."""
    if title and not GENERIC_TITLE.match(title):
        return title
    segs = [x for x in urlsplit(url).path.split("/") if x]
    if not segs:
        return title or url
    last = segs[-1]
    if last.lower() in SLUG_TOO_GENERIC and len(segs) > 1:
        prev = next((x for x in reversed(segs[:-1]) if x.lower() not in SLUG_PREFIX_SKIP and (len(x) > 3 or (len(x) >= 2 and x.isupper()))), None)
        if prev and prev.isupper() and len(prev) <= 4:  # a centre code such as IS / CR: keep it as a qualifier
            return f"{humanize(last)} ({prev})"
        return f"{humanize(prev)} — {humanize(last)}" if prev else humanize(last)
    parent = segs[-2] if len(segs) > 1 and segs[-2].lower() not in SLUG_PREFIX_SKIP and len(segs[-2]) > 3 else None
    return f"{humanize(parent)}: {humanize(last)}" if parent and parent.lower() != last.lower() else humanize(last)


def clean_popup(text: str | None) -> str | None:
    """Drop extraction debris from the Eligibility/Fees popups (stray counters, repeated section headers)."""
    if not text:
        return text
    lines = [ln for ln in text.split("\n") if not re.fullmatch(r"\d{1,2}", ln.strip())]
    while lines and lines[0].strip().lower() in ("admissions", "eligibility criteria", "admission", "fees", "fee structure"):
        lines.pop(0)
    return "\n".join(lines).strip() or None


def find_data_file(directory: Path, name: str) -> Path | None:
    """`name` or `name.gz` (deployment bundles are compressed)."""
    for cand in (directory / name, directory / (name + ".gz")):
        if cand.exists():
            return cand
    return None


def read_data_file(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    return path.read_text("utf-8")


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
    distinct: int = 0  # distinct programmes among candidates (campus listings / "(MBA)"-style naming variants count once)

    @property
    def ambiguous(self) -> bool:
        return self.distinct > 1


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
        pj, pg, mj = find_data_file(directory, "programmes.json"), find_data_file(directory, "pages.jsonl"), directory / "meta.json"
        if pj is None:
            raise DataNotReady(f"CHRIST website data not found in {directory}. Collect it with: python scrape.py "
                               "(for deployments, build the bundle with: python scrape.py bundle, then commit deploy_data/)")
        self.programmes = [Programme.model_validate(p) for p in json.loads(read_data_file(pj))]
        for p in self.programmes:
            p.eligibility, p.fees = clean_popup(p.eligibility), clean_popup(p.fees)
        self.by_id = {p.id: p for p in self.programmes}
        self.meta = json.loads(mj.read_text("utf-8")) if mj.exists() else {}
        self.pages = [json.loads(ln) for ln in read_data_file(pg).splitlines() if ln.strip()] if pg else []
        self.chunks: list[Chunk] = []
        self._build_chunks()
        self._build_index()
        self._build_name_index()

    # ------------------------------------------------------------------ build
    def _add(self, **kw):
        self.chunks.append(Chunk(**kw))

    def _build_chunks(self):
        for pg in self.pages:
            pg["title"] = page_title(pg.get("title", ""), pg["url"])
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
        # programme identity ignoring a name's own abbreviation: "Master of Business Administration" == "... (MBA)"
        self.name_key = {pid: frozenset(t for t in nt if not (t in ABBR_TOKENS and ABBR_TOKENS[t] <= nt)) for pid, nt in self.name_tokens.items()}

    # ------------------------------------------------------------------ search
    def search(self, query: str, *, k: int = 4, categories: set[str] | None = None, kinds: set[str] | None = None,
               programme_id: str | None = None, min_score: float = MIN_SCORE, url_rx: str | None = None) -> list[Hit]:
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
            if url_rx and not re.search(url_rx, c.url, re.I):
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
    def _units(self, text: str) -> list[tuple]:
        """What the user typed, as (token, tokens it stands for, idf mass, is_abbreviation). Words that appear in no
        programme name ("fees", "apply") are noise and are dropped."""
        units = []
        for t in dict.fromkeys(tokenize(text)):
            if t in ABBR:
                toks = {t} | ABBR_TOKENS[t]
                mass = max((self.name_idf.get(x, 0.0) for x in toks), default=0.0)
                if mass:
                    units.append((t, toks, mass, True))
            elif t in self.name_idf:
                units.append((t, {t}, self.name_idf[t], False))
        return units

    def _satisfies(self, pid: str, unit: tuple) -> bool:
        t, _toks, _mass, is_abbr = unit
        nt = self.name_tokens[pid]
        return (t in nt or ABBR_TOKENS[t] <= nt) if is_abbr else (t in nt)

    def search_programmes(self, query: str | None = None, level: str | None = None, campus: str | None = None, limit: int = 50) -> list[Programme]:
        """List programmes matching ALL typed words (abbreviations count by literal or full phrase); relax to ANY only if none do."""
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
        # everything the user typed is satisfied by these programmes (they only add more words): list them, don't pick one
        comps = [r[4] for r in rows if r[1] >= 0.999]
        if comps:
            return done(comps)
        best = rows[0][0]
        if best < 0.5:
            return Resolution(None, [])
        return done([r[4] for r in rows if r[0] >= best - 0.12])

    def source_for(self, p: Programme, label: str = "") -> SourceRef:
        return SourceRef(doc_id=p.url, title=f"{p.name}{' — ' + label if label else ''}", url=p.url)

    def stats(self) -> dict:
        return {"scraped_at": self.meta.get("scraped_at"), "programmes": len(self.programmes), "pages": len(self.pages),
                "site": self.meta.get("source_site"), "external_sources": self.meta.get("external_sources", []),
                "complete": self.meta.get("complete", True), "phase": self.meta.get("phase"),
                "pages_by_kind": self.meta.get("pages_by_kind", {})}


_corpus: Corpus | None = None
_stamp: tuple | None = None
_checked = 0.0
RELOAD_CHECK_S = 20.0


def data_dir() -> Path:
    """CHRIST_DATA_DIR if set; else the live scrape folder when present (local dev); else the committed deployment bundle (Vercel)."""
    if os.getenv("CHRIST_DATA_DIR"):
        return Path(os.environ["CHRIST_DATA_DIR"])
    return DEFAULT_DIR if find_data_file(DEFAULT_DIR, "programmes.json") else DEPLOY_DIR


def _files_stamp(d: Path) -> tuple:
    out = []
    for name in ("programmes.json", "programmes.json.gz", "pages.jsonl", "pages.jsonl.gz", "meta.json"):
        try:
            st = (d / name).stat()
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def get_corpus() -> Corpus:
    """Lazily load; transparently reload when the scraper has written newer data (checked at most every 20 s)."""
    import time
    global _corpus, _stamp, _checked
    d = data_dir()
    now = time.monotonic()
    if _corpus is None or (now - _checked) > RELOAD_CHECK_S:
        _checked = now
        stamp = _files_stamp(d)
        if _corpus is None or stamp != _stamp:
            try:
                _corpus = Corpus(d)
                _stamp = stamp
            except DataNotReady:
                if _corpus is None:
                    raise
            except Exception:  # a half-written file etc.: keep serving the previous corpus, retry next interval
                if _corpus is None:
                    raise
    return _corpus


def reset_corpus() -> None:
    global _corpus, _stamp, _checked
    _corpus, _stamp, _checked = None, None, 0.0
