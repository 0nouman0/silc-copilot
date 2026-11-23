"""Structured programme search with strict degree and specialization filtering.

Priority: exact degree match, exact specialization match, programme-name match, department match; related programmes only when the
applicant gave no strict filter. A requested degree is matched against `Program.degree_type`, never against the words of the name: "B.Tech"
never returns a BSc, and "B.Tech AI/ML" never returns a BSc in AI/ML. Duplicates (the same programme under several course ids) collapse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .indexes import tokens
from .models import Catalog, Program

# (regex, degree_type). Upper-case-only forms use (?-i:...) so the English words "be" and "ma" are not read as degrees.
DEGREE_PATTERNS: list[tuple[str, str]] = [
    (r"\bb\.?\s?tech\b|bachelor of technology", "B.Tech"), (r"\bb\.e\b|(?-i:\bBE\b)|bachelor of engineering", "B.E"),
    (r"\bb\.?\s?sc\b|bachelor of science", "BSc"), (r"\bbca\b|bachelor of computer applications", "BCA"), (r"\bbba\b|bachelor of business administration", "BBA"),
    (r"\bb\.?\s?com\b|bachelor of commerce", "B.Com"), (r"\bb\.a\b|(?-i:\bBA\b)|bachelor of arts", "BA"), (r"\bll\.?b\b|bachelor of laws?", "LLB"),
    (r"\bb\.?\s?ed\b", "B.Ed"), (r"\bm\.?\s?tech\b|master of technology", "M.Tech"), (r"\bm\.?\s?sc\b|master of science", "MSc"),
    (r"\bmba\b|master of business administration", "MBA"), (r"\bmca\b|master of computer applications", "MCA"), (r"\bm\.?\s?com\b|master of commerce", "M.Com"),
    (r"\bm\.a\b|(?-i:\bMA\b)|master of arts", "MA"), (r"\bll\.?m\b|master of laws?", "LLM"), (r"\bmsw\b|master of social work", "MSW"),
    (r"\bpg diploma\b|post[- ]?graduate diploma", "PG Diploma"), (r"\bph\.?\s?d\b|doctorate|doctor of philosophy", "PhD"),
]
LEVEL_PATTERNS = [(r"\b(undergraduate|under[- ]graduate|ug)\b|\bafter (my )?(12th|10th|\+2|class 12)\b", "Undergraduate"),
                  (r"\b(postgraduate|post[- ]graduate|pg|masters?)\b", "Postgraduate"), (r"\b(doctoral|doctorate)\b", "Doctoral")]
DEGREE_LEVEL = {"B.Tech": "Undergraduate", "B.E": "Undergraduate", "BSc": "Undergraduate", "BCA": "Undergraduate", "BBA": "Undergraduate", "B.Com": "Undergraduate",
                "BA": "Undergraduate", "LLB": "Undergraduate", "B.Ed": "Undergraduate", "M.Tech": "Postgraduate", "MSc": "Postgraduate", "MBA": "Postgraduate",
                "MCA": "Postgraduate", "M.Com": "Postgraduate", "MA": "Postgraduate", "LLM": "Postgraduate", "MSW": "Postgraduate", "PG Diploma": "Postgraduate", "PhD": "Doctoral"}


@dataclass(frozen=True)
class Concept:
    name: str
    rx: str
    required: frozenset[str]            # every token must be present in the programme's own name/field/specialization ...
    alt: frozenset[str] = frozenset()   # ... or any one of these (abbreviations written in the name, e.g. "AIML")
    kind: str = "field"                 # "field" replaces the whole topic; "spec" refines it


CONCEPTS: list[Concept] = [
    Concept("AI/ML", r"\bai\s*[/&-]?\s*(and\s+)?ml\b|\baiml\b|artificial intelligence\s*(and|&|/)?\s*machine learning", frozenset({"artificial", "intelligence", "machine", "learning"}), frozenset({"aiml"}), "spec"),
    Concept("Artificial Intelligence", r"\bai\b|artificial intelligence", frozenset({"artificial", "intelligence"}), frozenset({"ai"}), "spec"),
    Concept("Machine Learning", r"machine learning", frozenset({"machine", "learning"}), frozenset(), "spec"),
    Concept("Data Science", r"data science", frozenset({"data", "science"}), frozenset(), "spec"),
    Concept("Data Analytics", r"data analytics?", frozenset({"data", "analytics"}), frozenset({"analytic"}), "spec"),
    Concept("Cyber Security", r"cyber\s?security", frozenset({"cyber", "security"}), frozenset(), "spec"),
    Concept("IoT", r"\biot\b|internet of things", frozenset({"internet", "things"}), frozenset({"iot"}), "spec"),
    Concept("VLSI", r"\bvlsi\b", frozenset({"vlsi"}), frozenset(), "spec"),
    Concept("Computer Science and Engineering", r"\bcse\b|computer science (and|&) engineering", frozenset({"computer", "science", "engineering"}), frozenset({"cse"}), "field"),
    Concept("Computer Science", r"computer science|\bcs\b", frozenset({"computer", "science"}), frozenset(), "field"),
    Concept("Computers", r"\bcomputers?\b|\bcoding\b|\bprogramming\b", frozenset({"computer"}), frozenset(), "field"),
    Concept("Electronics and Communication", r"\bece\b|electronics (and|&) communication", frozenset({"electronics", "communication"}), frozenset({"ece"}), "field"),
    Concept("Electrical and Electronics", r"\beee\b|electrical (and|&) electronics", frozenset({"electrical", "electronics"}), frozenset({"eee"}), "field"),
]
NOISE = frozenset("""interested interest know about want wants programme programmes program programs course courses degree degrees only show me list available offer offered
please tell give any there do you have looking study studying like love option options what which hello hi hey thanks thank okay ok sure yes no undergraduate
postgraduate doctoral ug pg more other another different campus eligibility eligible fees fee curriculum syllabus detail details specific specialization specialisation
particular stream branch field area subject subjects need help can could would should get join apply admission admissions bachelor master masters bachelors""".split())
BROWSE_RX = re.compile(r"\b(show|list|see|view|display|all|what)\b.*\b(programmes?|programs?|courses?|degrees?)\b|\b(what|how) about\b|\binstead\b|\bswitch\b|\bchange (it )?to\b", re.I)
KEEP_RX = re.compile(r"\b(only|just|within|from (these|those|the above))\b", re.I)
RESET_RX = re.compile(r"\b(start over|start again|something else|something different|another (course|field|subject)|new search|forget (that|it))\b", re.I)


@dataclass
class Query:
    degree: str | None = None
    level: str | None = None
    concepts: tuple[Concept, ...] = ()
    words: tuple[str, ...] = ()         # validated subject words: HARD filters (every one must match the programme's own name/field/department)
    soft: tuple[str, ...] = ()          # keywords that only influence ranking, never exclude a programme
    campus: str | None = None           # a campus name, or a city ("Bangalore") meaning every campus there
    entry: str | None = None            # "lateral" | "regular": the admission route (None = either; a regular programme then notes its lateral twin)
    reset: bool = False
    browse: bool = False                # "show me BSc programmes" / "what about BCA?": a fresh listing, not a refinement
    keep: bool = False                  # "only ...": refine the current topic

    @property
    def strict(self) -> bool:
        return bool(self.degree or self.level or any(c.kind == "spec" for c in self.concepts))

    @property
    def has_filter(self) -> bool:
        return bool(self.degree or self.level or self.concepts or self.words or self.campus or self.entry)

    def topic_label(self) -> str:
        bits = [c.name for c in self.concepts] + [w for w in self.words]
        return " ".join(bits)


def campus_of(cat: Catalog | None, text: str) -> str | None:
    if cat is None:
        return None
    words = set(re.findall(r"[a-z]+", text.lower()))
    for c in cat.campuses.values():
        keys = {w for w in re.findall(r"[a-z]+", c.name.lower()) if w not in ("bangalore", "bengaluru", "campus", "christ", "road")}
        if keys & words:
            return c.name
    return None


def parse_query(text: str, cat: Catalog | None = None) -> Query:
    """What THIS message asks for (filters only; merging with the conversation happens in the dialog state)."""
    q = Query(reset=bool(RESET_RX.search(text)), browse=bool(BROWSE_RX.search(text)) and not KEEP_RX.search(text), keep=bool(KEEP_RX.search(text)))
    t = RESET_RX.sub(" ", text)
    for rx, deg in DEGREE_PATTERNS:                      # every degree named is removed from the words ("B.Tech and BSc"); the first is the filter
        if re.search(rx, t, re.I):
            q.degree = q.degree or deg
            t = re.sub(rx, " ", t, flags=re.I)
    if q.degree:
        q.level = DEGREE_LEVEL[q.degree]
    else:
        for rx, lv in LEVEL_PATTERNS:
            if re.search(rx, t, re.I):
                q.level = lv
                break
    found: list[Concept] = []
    for c in CONCEPTS:
        if re.search(c.rx, t, re.I):
            if c.name == "Artificial Intelligence" and any(x.name == "AI/ML" for x in found):
                continue
            if c.name == "Computer Science" and any(x.name == "Computer Science and Engineering" for x in found):
                continue
            if c.name == "Machine Learning" and any(x.name == "AI/ML" for x in found):
                continue
            found.append(c)
            t = re.sub(c.rx, " ", t, flags=re.I)
    q.concepts = tuple(found)
    q.campus = campus_of(cat, t)
    if q.campus:
        for w in re.findall(r"[a-z]+", q.campus.lower()):
            t = re.sub(rf"\b{w}\b", " ", t, flags=re.I)
    q.words = tuple(w for w in dict.fromkeys(tokens(t)) if w not in NOISE and len(w) > 2)
    return q


FACT_NAMES = ("eligibility", "fees", "dates", "curriculum", "selection", "placements", "apply", "details", "lateral")


def _strs(v, n: int, width: int) -> tuple[str, ...]:
    return tuple(str(x)[:width] for x in (v or []) if isinstance(x, str))[:n] if isinstance(v, list) else ()


def _clean_pending(p) -> dict | None:
    """A pending clarification sent back by the browser: only ids and numbers survive, and the server re-checks every id against the catalog."""
    if not isinstance(p, dict) or p.get("action") != "select_programme":
        return None
    opts = []
    for i, o in enumerate(p.get("options") or []):
        pid = o.get("programme_id") if isinstance(o, dict) else None
        if isinstance(pid, int) and not isinstance(pid, bool):
            opts.append({"number": len(opts) + 1, "programme_id": pid})
    req = p.get("requested") if p.get("requested") in FACT_NAMES else None
    return {"action": "select_programme", "requested": req, "options": opts[:12]} if opts else None


@dataclass
class State:
    degree: str | None = None
    level: str | None = None
    concepts: tuple[str, ...] = ()        # concept names (resolved back through CONCEPTS)
    words: tuple[str, ...] = ()
    campus: str | None = None
    programme_id: int | None = None       # the programme currently being discussed
    entry: str | None = None              # "lateral" | "regular" | None: the admission route the applicant asked about
    last_ids: tuple[int, ...] = ()        # programme ids of the result list the applicant last saw (what "fees list" / "the first one" refer to)
    shown: int = 0                        # how many of last_ids have been listed to the applicant ("show more" continues from here)
    topic: str | None = None              # what the conversation is about right now ("B.Tech", "MBA", "admission", "university")
    answered: tuple[str, ...] = ()        # information already explained in this conversation (not repeated unless asked again)
    asked: tuple[str, ...] = ()           # clarifying questions already asked (never asked twice)
    suggested: tuple[str, ...] = ()       # the quick replies last shown (never repeated back to back)
    pending: dict | None = None           # {"action": "select_programme", "requested": "fees"|None, "options": [{"number": 1, "programme_id": 81}, ...]}
    last_intent: str | None = None

    def as_dict(self) -> dict:
        return {"degree": self.degree, "level": self.level, "concepts": list(self.concepts), "words": list(self.words), "campus": self.campus, "programme_id": self.programme_id, "entry": self.entry,
                "last_ids": list(self.last_ids), "pending": self.pending, "last_intent": self.last_intent, "shown": self.shown, "topic": self.topic,
                "answered": list(self.answered), "asked": list(self.asked), "suggested": list(self.suggested)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "State":
        d = d or {}
        names = {c.name for c in CONCEPTS}
        return cls(degree=d.get("degree") if d.get("degree") in set(DEGREE_LEVEL) else None,
                   level=d.get("level") if d.get("level") in ("Undergraduate", "Postgraduate", "Doctoral") else None,
                   concepts=tuple(x for x in (d.get("concepts") or []) if x in names)[:4], words=tuple(str(w)[:30] for w in (d.get("words") or []))[:5],
                   campus=str(d["campus"])[:60] if d.get("campus") else None, programme_id=int(d["programme_id"]) if isinstance(d.get("programme_id"), int) and not isinstance(d.get("programme_id"), bool) else None,
                   entry=d.get("entry") if d.get("entry") in ("lateral", "regular") else None,
                   last_ids=tuple(i for i in (d.get("last_ids") or []) if isinstance(i, int) and not isinstance(i, bool))[:30], pending=_clean_pending(d.get("pending")),
                   last_intent=str(d["last_intent"])[:40] if d.get("last_intent") else None,
                   shown=d["shown"] if isinstance(d.get("shown"), int) and not isinstance(d.get("shown"), bool) and 0 <= d["shown"] <= 60 else 0,
                   topic=str(d["topic"])[:60] if d.get("topic") else None,
                   answered=_strs(d.get("answered"), 24, 60), asked=_strs(d.get("asked"), 12, 40), suggested=_strs(d.get("suggested"), 4, 120))

    def concept_objs(self) -> tuple[Concept, ...]:
        by = {c.name: c for c in CONCEPTS}
        return tuple(by[n] for n in self.concepts if n in by)

    def query(self) -> Query:
        return Query(self.degree, self.level, self.concept_objs(), self.words, (), self.campus, self.entry)

    @property
    def has_topic(self) -> bool:
        return bool(self.degree or self.level or self.concepts or self.words or self.campus or self.entry)


def merge(state: State, q: Query) -> State:
    """Apply one message's filters to the conversation state. A new field replaces the old topic, a specialization refines it, a degree is kept
    until another degree is named."""
    s = replace(state)
    if q.reset:
        s = State(campus=s.campus)
    changed = False
    if q.degree and s.degree and q.degree != s.degree and not q.entry:
        s.entry = None                                   # switching from one degree to another starts from the regular route again
    if q.degree and q.degree != s.degree and not q.concepts and not q.words and (q.browse or (s.degree and not q.keep)):
        s.concepts, s.words, changed = (), (), True      # "What about BCA?" / "show me BSc programmes" after B.Tech AI/ML: a fresh listing, not "BCA AI/ML"
    if q.degree:
        changed |= q.degree != s.degree
        s.degree, s.level = q.degree, q.level
    elif q.level:
        changed |= q.level != s.level
        s.level, s.degree = q.level, None if q.level != s.level else s.degree
    new_field = [c for c in q.concepts if c.kind == "field"]
    new_spec = [c for c in q.concepts if c.kind == "spec"]
    if new_field or (q.words and not q.concepts):
        new_c, new_w = tuple(c.name for c in new_field), q.words
        changed |= (new_c, new_w) != (s.concepts, s.words)         # saying the same thing again changes nothing (and keeps what was already asked)
        s.concepts, s.words = new_c, new_w
    if new_spec:
        kept = [n for n in s.concepts if next((c for c in CONCEPTS if c.name == n), None) and next(c for c in CONCEPTS if c.name == n).kind == "field"]
        s.concepts = tuple(kept + [c.name for c in new_spec])
        if q.words:
            s.words = q.words
        changed = True
    if q.words and q.concepts and not new_field and not new_spec:
        s.words = q.words
    if q.campus:
        s.campus = q.campus
        changed = True
    if q.entry:
        changed |= q.entry != s.entry
        s.entry = q.entry
    if changed:
        s.programme_id, s.pending, s.last_ids, s.shown = None, None, (), 0
        s.asked = ()                                     # a changed topic may need its questions again
    return s


# ---------------------------------------------------------------------------------------------------- matching
@dataclass
class Group:
    rep: Program                        # representative listing (richest, lowest id)
    ids: tuple[int, ...]
    campus_ids: tuple[int, ...]
    score: float = 0.0
    lateral_also: bool = False          # the regular programme also has a lateral-entry listing (shown once, not as a second programme)


def _own_tokens(p: Program) -> set[str]:
    return set(tokens(" ".join([p.name, p.field, p.specialization or "", *p.subjects])))


def _primary_tokens(p: Program) -> set[str]:
    return set(tokens(" ".join([p.field, p.specialization or "", *p.subjects])))


def _satisfies(c: Concept, toks: set[str]) -> bool:
    return c.required <= toks or bool(c.alt & toks)


def _campus_ids(cat: Catalog, name: str | None) -> set[int] | None:
    if not name:
        return None
    exact = {c.id for c in cat.campuses.values() if c.name.lower() == name.lower()}
    return exact or {c.id for c in cat.campuses.values() if name.lower() in c.name.lower()}     # "Bangalore" = every Bangalore campus


def find_programmes(cat: Catalog, q: Query, limit: int = 60) -> list[Group]:
    """Strict, deduplicated, ranked matches. Every filter in `q` must hold."""
    campus_ids = _campus_ids(cat, q.campus)
    scored: dict[tuple, list[tuple[float, Program]]] = {}
    for p in cat.programs.values():
        if q.degree and p.degree_type != q.degree:
            continue
        if q.level and p.academic_level != q.level:
            continue
        if q.entry and p.entry != q.entry:
            continue
        if campus_ids is not None and not ({p.campus_id} | {pl.campus_id for pl in p.placements}) & campus_ids:
            continue
        own, prim = _own_tokens(p), _primary_tokens(p)
        score, ok = 0.0, True
        has_spec = any(c.kind == "spec" for c in q.concepts)
        for c in q.concepts:
            if not _satisfies(c, own):
                if c.kind == "field" and has_spec:
                    continue                    # the specialization is the strict filter; the broader field only ranks
                ok = False
                break
            score += 10 if _satisfies(c, prim) else 4
        if not ok:
            continue
        name_t = set(tokens(p.name))
        dept = cat.departments.get(p.department_id) if p.department_id else None
        dept_t = set(tokens(dept.name)) if dept else set()
        for w in q.words:
            if w in own or w in name_t:
                score += 3
            elif w in dept_t:
                score += 1
            else:
                ok = False
                break
        if not ok:
            continue
        for w in q.soft:                                 # soft keywords only rank
            if w in own or w in name_t:
                score += 1.5
            elif w in dept_t:
                score += 0.5
        score += 2 if p.entry == "regular" else (-3 if not q.entry else 0)
        score -= len(p.name) / 400
        scored.setdefault(p.dedupe_key, []).append((score, p))
    groups: list[Group] = []
    for rows in scored.values():
        rows.sort(key=lambda r: (-r[0], -sum(len(v) for v in r[1].sections.values()), r[1].id))
        best = rows[0][1]
        campuses = tuple(sorted({best.campus_id, *(pl.campus_id for r in rows for pl in r[1].placements), *(r[1].campus_id for r in rows)} - {0}))
        groups.append(Group(best, tuple(sorted(r[1].id for r in rows)), campuses, rows[0][0], best.entry == "regular" and any(r[1].entry == "lateral" for r in rows)))
    groups.sort(key=lambda g: (-g.score, g.rep.name))
    return groups[:limit]


def make_group(rows: list[Program]) -> Group:
    """One entry for a programme listed under several course ids: the regular listing leads, a lateral twin is a flag."""
    rows = sorted(rows, key=lambda p: (p.entry != "regular", -sum(len(v) for v in p.sections.values()), p.id))
    campuses = {pl.campus_id for p in rows for pl in p.placements} | {p.campus_id for p in rows}
    return Group(rows[0], tuple(sorted(p.id for p in rows)), tuple(sorted(campuses - {0})), 0.0, rows[0].entry == "regular" and any(p.entry == "lateral" for p in rows))


def group_for(cat: Catalog, pid: int | None) -> Group | None:
    p = cat.programs.get(pid) if pid else None
    if p is None:
        return None
    return make_group([x for x in cat.programs.values() if x.dedupe_key == p.dedupe_key and x.entry == p.entry])


def related(cat: Catalog, text: str, limit: int = 6) -> list[Group]:
    """Related programmes by content, used ONLY when the applicant gave no strict filter."""
    seen: dict[tuple, Group] = {}
    for ch in cat.indexes.search(text, 40, kinds={"overview", "careers", "what_you_learn", "why_choose", "specialization", "highlights"}):
        p = cat.programs.get(ch.program_id) if ch.program_id else None
        if p and (p.dedupe_key, p.entry) not in seen:
            seen[(p.dedupe_key, p.entry)] = Group(p, (p.id,), (p.campus_id,))
        if len(seen) >= limit:
            break
    return list(seen.values())


def facets(groups: list[Group], attr: str) -> list[tuple[str, int]]:
    """Counts of a programme attribute among the results (used to suggest the next narrowing step)."""
    from collections import Counter
    c = Counter((getattr(g.rep, attr) or "").strip() for g in groups)
    c.pop("", None)
    return c.most_common()
