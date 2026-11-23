"""In-memory indexes: key lookups (alias, filter, topic, ERP id) and BM25 keyword search. No vector database.

Embeddings are optional and only worth adding if the evaluation shows discovery questions failing; the slot is `Indexes.embeddings`
(one NumPy matrix over the same chunks) and is None by default.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .models import Provenance

_STOP = frozenset("the a an of to is are and or in on for i my do does what how can it me with if be this that at by from will there you your we our any "
                  "about which who when where please tell show give want need get have has am was so as its".split())


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith("es") and t[-3] in "sxz":
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


_BE = re.compile(r"\bB\.E\b\.?|\bb\.e\b\.?|\bBE\b")


def tokens(text: str) -> list[str]:
    text = _BE.sub(" engineering ", text or "")
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", "", (text or "").lower()).replace(".", "")
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in _STOP and len(t) > 1]


def norm_name(text: str) -> str:
    """Key for the alias index: 'B.B.A', 'bba' and ' BBA ' are the same key."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


@dataclass(frozen=True)
class Chunk:
    id: int
    text: str
    title: str
    kind: str                           # section kind, "topic", "document", "overview"...
    program_id: int | None = None
    topic: str | None = None
    campus_id: int | None = None
    url: str | None = None
    provenance: Provenance | None = None
    expired: bool = False
    program_ids: frozenset[int] | None = None   # shared sections: stored once, valid for many programmes


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.post: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(self.tf):
            for t in c:
                self.post[t].append(i)

    def search(self, query: list[str], k: int = 5, accept: Callable[[int], bool] | None = None) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        for t in dict.fromkeys(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.post[t]:
                if accept and not accept(i):
                    continue
                f = self.tf[i][t]
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / (self.avg or 1)))
        return sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:k]


@dataclass
class Indexes:
    by_alias: dict[str, tuple[int, ...]] = field(default_factory=dict)
    by_erp_id: dict[str, int] = field(default_factory=dict)
    by_topic: dict[tuple[str, int | None], tuple[Any, ...]] = field(default_factory=dict)
    by_level: dict[str, frozenset[int]] = field(default_factory=dict)
    by_campus: dict[int, frozenset[int]] = field(default_factory=dict)
    by_school: dict[int, frozenset[int]] = field(default_factory=dict)
    by_department: dict[int, frozenset[int]] = field(default_factory=dict)
    chunks: tuple[Chunk, ...] = ()
    bm25: BM25 | None = None
    embeddings: Any = None              # optional NumPy matrix, off by default

    def by_filter(self, level: str | None = None, campus_id: int | None = None, school_id: int | None = None,
                  department_id: int | None = None, universe: Iterable[int] | None = None) -> list[int]:
        """Program ids matching every given dimension (None = any)."""
        sets: list[frozenset[int]] = []
        if level:
            sets.append(self.by_level.get(level, frozenset()))
        if campus_id is not None:
            sets.append(self.by_campus.get(campus_id, frozenset()))
        if school_id is not None:
            sets.append(self.by_school.get(school_id, frozenset()))
        if department_id is not None:
            sets.append(self.by_department.get(department_id, frozenset()))
        if universe is not None:
            sets.append(frozenset(universe))
        if not sets:
            return []
        return sorted(frozenset.intersection(*sets))

    def search(self, query: str, k: int = 5, *, program_id: int | None = None, topic: str | None = None,
               campus_id: int | None = None, kinds: set[str] | None = None) -> list[Chunk]:
        """BM25 over current sections, topic items and extracted PDF text. Optional filters narrow the pool."""
        if not self.bm25:
            return []

        def ok(i: int) -> bool:
            c = self.chunks[i]
            return ((program_id is None or c.program_id == program_id or (c.program_ids is not None and program_id in c.program_ids)) and (topic is None or c.topic == topic)
                    and (campus_id is None or c.campus_id in (None, campus_id)) and (kinds is None or c.kind in kinds))

        return [self.chunks[i] for i, _ in self.bm25.search(tokens(query), k, ok)]


def chunk_text(text: str, limit: int = 1100) -> list[str]:
    """Split on paragraph boundaries so every chunk stays a readable passage."""
    out, cur = [], ""
    for para in (p for p in text.split("\n") if p.strip()):
        if cur and len(cur) + len(para) > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
        while len(cur) > limit * 2:
            out.append(cur[:limit])
            cur = cur[limit:]
    if cur.strip():
        out.append(cur)
    return out


def build_indexes(programs: dict[int, Any], topics: dict[str, tuple[Any, ...]], aliases: dict[str, set[int]], level_of: Callable[[Any], str],
                  chunks: list[Chunk]) -> Indexes:
    ix = Indexes()
    by_alias: dict[str, set[int]] = defaultdict(set)
    for k, ids in aliases.items():
        by_alias[k] |= ids
    by_level, by_campus, by_school, by_dept = (defaultdict(set) for _ in range(4))
    for pid, p in programs.items():
        by_alias[norm_name(p.name)].add(pid)
        if p.erp_program_id:
            ix.by_erp_id[p.erp_program_id] = pid
        by_level[level_of(p)].add(pid)
        for pl in p.placements or ():
            by_campus[pl.campus_id].add(pid)
            if pl.department_id:
                by_dept[pl.department_id].add(pid)
        by_campus[p.campus_id].add(pid)
        if p.school_id:
            by_school[p.school_id].add(pid)
    ix.by_alias = {k: tuple(sorted(v)) for k, v in by_alias.items() if k}
    ix.by_level = {k: frozenset(v) for k, v in by_level.items()}
    ix.by_campus = {k: frozenset(v) for k, v in by_campus.items()}
    ix.by_school = {k: frozenset(v) for k, v in by_school.items()}
    ix.by_department = {k: frozenset(v) for k, v in by_dept.items()}
    for topic, items in topics.items():
        buckets: dict[int | None, list] = defaultdict(list)
        for it in items:
            buckets[it.campus_id].append(it)
        for cid, its in buckets.items():
            ix.by_topic[(topic, cid)] = tuple(its)
    ix.chunks = tuple(chunks)
    ix.bm25 = BM25([tokens(f"{c.title} {c.text}") for c in chunks])
    return ix
