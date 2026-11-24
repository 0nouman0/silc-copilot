"""Language understanding for every message: spell correction, intent and entity extraction as a SCHEMA-VALIDATED structure.

Two interpreters feed the same validated `Interpretation`:

* the AI interpreter (a chat model, `UNDERSTANDING_MODE=auto|llm`): reads the whole sentence, corrects spelling, picks the intent and the entities and returns JSON;
* the local interpreter: a vocabulary-driven corrector (edit distance against the university's own taxonomy plus a small conversational lexicon, with
  context) and the same entity parser. It always runs first, is the fallback when no model is available or its answer is invalid, and is the
  safety net for hard constraints.

Nothing the model says reaches a database filter unchecked: degree, level, campus, entry route, concepts and subject words are validated against the
real taxonomy (degrees and campuses present in the catalog, known concepts, words that occur in programme names or content). Unknown values are
dropped and reported, misspelt filler words never become filters, and an explicit degree or route in the user's own words overrides the model.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..catalog.indexes import _stem, tokens
from ..catalog.models import Catalog
from ..catalog.search import CONCEPTS, DEGREE_LEVEL, DEGREE_PATTERNS, FACT_NAMES, Concept, Query, parse_query
from . import conversation as conv
from .knowledge import degree_terms, glossary_terms
from .smalltalk import smalltalk_kind

log = logging.getLogger("understanding")

INTENTS = ("greeting", "smalltalk", "general_conversation", "programme_count", "concept_question", "comparison", "programme_overview", "programme_search", "availability_check",
           "programme_info", "admission_process", "lateral_entry", "campus_info", "facilities", "scholarship", "selection", "clarification_needed", "unsupported", "other")

# ------------------------------------------------------------------------------------------------ vocabulary
FACT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("eligibility", re.compile(r"eligib|criteria|qualif|requirements?|who can (apply|join)|can i (apply|join|get|take)", re.I)),
    ("fees", re.compile(r"\bfees?\b|tuition|how much|\bcost\b|\bprice\b", re.I)),
    ("dates", re.compile(r"last date|deadline|application (date|open|close)|\bdates?\b|when (does|do|is|can)\b.*\b(apply|open|start|close)", re.I)),
    ("curriculum", re.compile(r"curriculum|syllabus|subjects?\b|modules?|what (will|do) i (study|learn)|course structure|semester", re.I)),
    ("selection", re.compile(r"selection|entrance|admission test|how (are|is) .* selected", re.I)),
    ("placements", re.compile(r"placements?|recruit|packages?|salary|career", re.I)),
    ("apply", re.compile(r"how (do|to|can) i apply|application process|admission process|apply for|how .* (admission|apply)|procedure", re.I)),
    ("details", re.compile(r"tell me (more )?about|\bdetails?\b|overview|duration|how long|about (it|this|that|the (course|programme|program))|more info", re.I)),
]
OTHER_TOPIC_RX = re.compile(r"\b(hostel|accommodation|dining|mess|canteen|cafeteria|transport|bus|library|medical|clinic|scholarship|contact|phone|email|e-mail|where is|"
                            r"refund|payment|pay|status|result|offer letter|admit card|documents?|ragging|grievance|nri|international)\b", re.I)
STATS_RX = re.compile(r"\bhow many\b.*\b(programmes?|programs?|courses?|degrees?|listings?|campuses|campus|specializations?|specialisations?)\b|\b(number|count|total)\s+of\s+(programmes?|programs?|courses?|degrees?|listings?|campuses)\b|\btotal\s+(programmes?|programs?|courses?|listings?)\b", re.I)
CONCEPT_RX = re.compile(r"\bwhat\s+(is|are|does|do)\b|\bwhat'?s\b|\bexplain\b|\bmeaning of\b|\bdefine\b|\bwhat does .* mean\b|\bdifference(s)? between\b|\bdiffer\b|\bvs\.?\b|\bversus\b|\bwhich is better\b|\bconfused between\b|\bunderstand\b", re.I)
COMPARE_RX = re.compile(r"\bcompare\b|\bcomparison\b|\bside by side\b|\bin a table\b", re.I)
ADMISSION_RX = re.compile(r"\bhow (do|can|to|should)\s*(i|we)?\s*(get|apply|join|enrol+|enroll|take admission|seek admission|gain admission)\b|\bhow to (apply|get (admission|in|into)|join|enrol+)\b|"
                          r"\badmission (process|procedure|requirements?|steps)\b|\bapplication (process|procedure|steps)\b|\bget (admission|into)\b|\bhow (do|can) i (get )?(admission|admitted)\b|\bhow do i get into\b", re.I)
BROWSE_RX = re.compile(r"\b(show|list|browse|see|view|display)\b|\boptions\b|\bavailable\b|\b(offered|offer)\b|\b(what|which)\b.*\b(programmes?|programs?|courses?|degrees?|specializations?|specialisations?)\b|"
                       r"\bknow about\b.*\b(programmes?|programs?|courses?)\b|\bwhat .* (do you|does christ) (have|offer)\b", re.I)
OVERVIEW_RX = re.compile(r"\btell me (more )?about\b|\b(more )?(info|information|details?) (on|about|of)\b|\boverview\b|\bdescribe\b|\b(want|like) to know about\b|\bknow about\b", re.I)
AVAIL_RX = re.compile(r"\bdo you (have|offer)\b|\bis there\b|\b(is|are) .{1,40}(available|offered)\b|\bdoes (christ|the university|it) (have|offer)\b|\bany .{1,25}(programmes?|courses?)\b", re.I)
CAMPUS_RX = re.compile(r"\b(which|what) (campus|campuses|city|location)\b|\bwhere (is|are) (it|this|that|they|the .{1,30}) (offered|located|available)\b|\bcampus(es)? (list|options)\b|\bwhere can i (study|do)\b", re.I)
# campus life: hostel, dining, library, transport... answered from the campus facility records, never from a programme search
FACILITY_STRONG = re.compile(r"\b(hostels?|accommodations?|dormitor(?:y|ies)|residences?|dining|mess|canteens?|cafeterias?|food court|shuttle|clinic|infirmary|wi-?fi|facilit(?:y|ies)|infrastructure)\b", re.I)
FACILITY_WEAK = re.compile(r"\b(libraries|library|laborator(?:y|ies)|labs?|transport\w*|bus(?:es)?|medical|sports?|gym|auditorium)\b", re.I)   # also part of programme names, so only without a programme in the sentence


def is_facility_question(text: str, programme_words: bool = False) -> bool:
    return bool(FACILITY_STRONG.search(text) or (FACILITY_WEAK.search(text) and not programme_words))


SCHOLAR_RX = re.compile(r"\bscholarships?\b|\bfinancial aid\b|\bfee concession\b|\bwaiver\b", re.I)
# entry route: synonyms of lateral entry in applicants' words (the university's own term is "Lateral Entry")
_LATERAL_RX = re.compile(r"\blateral\b|direct(ly)?\s+(admission\s+)?((to|into|in)\s+)?(the\s+)?(2nd|second|3rd|third)\s+(year|yr)|(2nd|second)[- ]?(year|yr)\s+(admission|entry|direct|joining|join)|"
                         r"join\s+(directly\s+)?(in\s+)?(the\s+)?(2nd|second)\s+(year|yr)|\bdiploma\b.*\b(admission|join|joining|eligible|entry|apply|after|holders?|students?)\b|after\s+(a\s+)?(3|three)[- ]?years?\s+diploma|diploma\s+holders?", re.I)
_REGULAR_RX = re.compile(r"\b(regular|normal|first[- ]year entry|not lateral|without lateral|non[- ]lateral)\b", re.I)

CONVERSATIONAL = set("""i am im want wants wish like love learn study studying know tell me please about show give find can could you what is are the in for and course courses
interested interest looking need needs help let lets see list do does did get join take pursue choose pick option options available offer offered which who where when how why
programme programmes program programs degree degrees only just also any some something anything everything all this that these those it its my your our we they there here
would should will have has had be been to of on at by from with without not no yes ok okay hi hello hey thanks thank bye please sir madam tech b e btech one two
christ university college institute institution after before during years yr yrs months since until bangalore bengaluru pune delhi ncr lavasa kengeri central yeshwanthpur bannerghatta campus campuses""".split())
_CONV_STEMS = {_stem(w) for w in CONVERSATIONAL}
ENTRY_WORDS = {"lateral", "entry", "direct", "directly", "second", "2nd", "year", "diploma", "holders", "joining", "admission"}
LEXICON = set("""want wants wish like love learn study studying know tell show give find can could you what which who where when how why please about interested interest looking need help
join take pursue choose option options available offer offered programme programmes program programs course courses degree degrees only just also lateral entry direct second year diploma
admission apply application process eligibility eligible fees fee curriculum syllabus engineering technology computer science commerce business administration management psychology
bachelor master doctorate campus bangalore bengaluru pune delhi kengeri central yeshwanthpur bannerghatta lavasa hostel specialization specialisation stream branch subject subjects
career careers placement placements scholarship documents required procedure semester duration details information regular""".split())
COMMON = set("""fine good great well okay alright nice cool thanks thank hello hey yes sure maybe sorry today tomorrow yesterday weather time name help doing exploring looking around fun
interesting confused decide decision choose better best difference between compare comparison versus explain meaning mean define example simple easy hard difficult future career job jobs money
students student parents school college class classes board marks percentage percent result results exam exams test tests online offline hostel food transport library sports
bachelor masters doctoral undergraduate postgraduate honours diploma certificate""".split())
VERBS = {"want", "wish", "like", "learn", "study", "know", "join", "take", "apply", "get", "need", "love", "pursue", "choose", "see", "do", "go", "find", "show", "tell"}
AMBIGUOUS_ALIAS = {"tech": "B.Tech", "technology": "B.Tech"}      # "tech courses": probably B.Tech, but ask before assuming
_WORD = re.compile(r"[A-Za-z]+")


# ------------------------------------------------------------------------------------------------ taxonomy
@dataclass
class Taxonomy:
    degrees: tuple[str, ...]
    campuses: tuple[str, ...]
    cities: tuple[str, ...]
    concepts: tuple[str, ...]
    topic_stems: frozenset[str]
    content_stems: frozenset[str]
    spell_vocab: dict[str, float]
    known_words: Counter


_TAX: dict[int, Taxonomy] = {}


def taxonomy_for(cat: Catalog | None) -> Taxonomy:
    """What exists in THIS university: used to validate entities and to spell-correct against real programme words."""
    k = id(cat) if cat is not None else 0
    if k in _TAX:
        return _TAX[k]
    spell: dict[str, float] = {w: 1.0 for w in COMMON}
    spell.update({w: 5.0 for w in LEXICON})
    topic: set[str] = set()
    known: Counter = Counter()
    degrees: set[str] = set()
    campuses: list[str] = []
    cities: set[str] = set()
    content: set[str] = set()
    if cat is not None:
        for p in cat.programs.values():
            degrees.add(p.degree_type)
            blob = " ".join([p.name, p.field, p.specialization or "", *p.subjects])
            topic.update(tokens(blob))
            for w in _WORD.findall(blob):
                if len(w) >= 3:
                    spell.setdefault(w.lower(), 2.0)
        for d in cat.departments.values():
            for w in _WORD.findall(d.name):
                if len(w) >= 3:
                    spell.setdefault(w.lower(), 2.0)
        for c in cat.campuses.values():
            campuses.append(c.name)
            for w in _WORD.findall(c.name):
                if len(w) >= 3:
                    spell.setdefault(w.lower(), 3.0)
                    cities.add(w.lower())
        if cat.indexes.bm25 is not None:
            content = set(cat.indexes.bm25.idf)
            for ch in cat.indexes.chunks:
                known.update(w.lower() for w in _WORD.findall(ch.text) if len(w) >= 3)
    tax = Taxonomy(tuple(sorted(d for d in degrees if d in DEGREE_LEVEL)) or tuple(DEGREE_LEVEL), tuple(campuses), tuple(sorted(cities & {"bangalore", "bengaluru", "pune", "delhi", "ncr", "lavasa"})),
                   tuple(c.name for c in CONCEPTS), frozenset(topic), frozenset(content), spell, known)
    _TAX.clear()
    _TAX[k] = tax
    return tax


# ------------------------------------------------------------------------------------------------ spelling
def osa(a: str, b: str) -> int:
    """Edit distance with adjacent transpositions ('waht' -> 'want' is 1)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def normalise_case(text: str) -> tuple[str, bool]:
    """ALL-CAPS input (caps lock, voice tools) is lower-cased so 'TO BE' is not read as the degree B.E; a dotted 'B.E' still is."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) >= 0.7:
        return text.lower(), True
    return text, False


_PLAIN = frozenset("there their these those three other another about which while where when whose whom whole after before again could would should might shall until since being been "
                   "hostel hostels dining library transport shuttle clinic".split())      # ordinary English words are never 'corrected' into a different ordinary word ("is there" -> "is where")


def _known(word: str, tax: Taxonomy) -> bool:
    return word in _PLAIN or word in tax.spell_vocab or tax.known_words.get(word, 0) >= 3


def correct_word(word: str, prev: str | None, nxt: str | None, tax: Taxonomy) -> str | None:
    """The intended word for a probable misspelling, or None (valid word, too short, or no clearly best candidate)."""
    w = word.lower()
    if len(w) < 4 or _known(w, tax):
        return None
    maxd = 1 if len(w) <= 5 else 2
    scored: list[tuple[float, str]] = []
    for cand, prior in tax.spell_vocab.items():
        if abs(len(cand) - len(w)) > maxd or cand == w:
            continue
        d = osa(w, cand)
        if d > maxd:
            continue
        s = prior - 10 * d
        if prev in ("i", "we", "to", "would", "can", "will", "you", "not", "dont", "don't") and cand in VERBS:
            s += 4
        if nxt in ("is", "are", "does", "do", "about", "for", "of") and cand in ("what", "how", "which", "where", "when"):
            s += 4
        scored.append((s, cand))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.01:
        return None                                     # two equally good guesses: do not guess
    return scored[0][1]


def spell_correct(text: str, tax: Taxonomy) -> tuple[str, list[tuple[str, str]]]:
    ms = list(_WORD.finditer(text))
    lows = [m.group(0).lower() for m in ms]
    out, last, changes = [], 0, []
    for i, m in enumerate(ms):
        fix = correct_word(m.group(0), lows[i - 1] if i else None, lows[i + 1] if i + 1 < len(lows) else None, tax)
        if fix:
            if m.group(0).isupper():
                fix = fix.upper()
            elif m.group(0)[0].isupper():
                fix = fix.capitalize()
            out.append(text[last:m.start()] + fix)
            last = m.end()
            changes.append((m.group(0), fix))
    return "".join(out) + text[last:], changes


# ------------------------------------------------------------------------------------------------ the structure
class Interpretation(BaseModel):
    original_message: str
    corrected_message: str
    intent: Literal["greeting", "smalltalk", "general_conversation", "programme_count", "concept_question", "comparison", "programme_overview", "programme_search", "availability_check",
                    "programme_info", "admission_process", "lateral_entry", "campus_info", "facilities", "scholarship", "selection", "clarification_needed", "unsupported", "other"]
    smalltalk: str | None = None
    scope: Literal["university", "degree", "programme", "none"] = "none"            # what the question is about: the whole university, a degree family, or a programme
    requested_information: list[str] = Field(default_factory=list)
    requires_programme_search: bool = False
    response_mode: Literal["conversational", "list", "statistics", "comparison_table", "clarification"] = "conversational"
    explicit_browse: bool = False                                                    # the user asked to SEE options (show / list / what ... available)
    compare_degrees: list[str] = Field(default_factory=list)
    concept_terms: list[str] = Field(default_factory=list)                          # glossary keys of the terms to explain
    converse_kind: str | None = None
    requested_info: str | None = None
    degree_type: str | None = None
    academic_level: str | None = None
    concepts: list[str] = Field(default_factory=list)          # validated taxonomy concepts (field and specialization)
    hard_words: list[str] = Field(default_factory=list)        # validated subject words that must match
    soft_words: list[str] = Field(default_factory=list)        # keywords that only rank
    campus: str | None = None
    entry_type: Literal["lateral", "regular"] | None = None
    selection: int | None = None
    refers_to_active_programme: bool = False
    irrelevant_tokens: list[str] = Field(default_factory=list)
    corrections: list[tuple[str, str]] = Field(default_factory=list)
    reset: bool = False
    browse: bool = False
    keep: bool = False
    clarification: str | None = None
    clarification_options: list[str] = Field(default_factory=list)
    confidence: float = 0.9
    source: Literal["local", "llm"] = "local"
    notes: list[str] = Field(default_factory=list)

    def to_query(self) -> Query:
        by = {c.name: c for c in CONCEPTS}
        return Query(degree=self.degree_type, level=self.academic_level, concepts=tuple(by[n] for n in self.concepts if n in by), words=tuple(self.hard_words),
                     soft=tuple(self.soft_words), campus=self.campus, entry=self.entry_type, reset=self.reset, browse=self.browse, keep=self.keep)

    @property
    def has_filter(self) -> bool:
        return bool(self.degree_type or self.academic_level or self.concepts or self.hard_words or self.campus or self.entry_type)

    def unknown_tokens(self) -> list[str]:
        """Words that are neither filler nor a known subject: they are left out of the search, and the answer says so instead of silently ignoring them."""
        return [t for t in self.irrelevant_tokens if t.lower() not in CONVERSATIONAL and _stem(t.lower()) not in _CONV_STEMS and t.lower() not in ENTRY_WORDS and not t.isdigit()]

    def public(self) -> dict[str, Any]:
        by = {c.name: c for c in CONCEPTS}
        disc = [n for n in self.concepts if by[n].kind == "field"] + list(self.hard_words)
        spec = [n for n in self.concepts if by[n].kind == "spec"]
        hard = [n for n, v in (("degree_type", self.degree_type), ("academic_level", self.academic_level), ("campus", self.campus), ("entry_type", self.entry_type),
                               ("specialization", spec), ("discipline", disc)) if v]
        return {"original_message": self.original_message, "corrected_message": self.corrected_message, "intent": self.intent, "requested_info": self.requested_info,
                "scope": self.scope, "requested_information": self.requested_information, "requires_programme_search": self.requires_programme_search, "response_mode": self.response_mode,
                "degree_type": self.degree_type, "academic_level": self.academic_level, "discipline": " ".join(disc) or None, "specialization": ", ".join(spec) or None,
                "campus": self.campus, "entry_type": self.entry_type, "search_keywords": [*self.concepts, *self.hard_words, *self.soft_words], "hard_constraints": hard,
                "irrelevant_tokens": self.irrelevant_tokens, "corrections": [list(c) for c in self.corrections], "confidence": round(self.confidence, 2), "source": self.source,
                "clarification": self.clarification}


# ------------------------------------------------------------------------------------------------ local interpreter
def _classify_words(words: tuple[str, ...], tax: Taxonomy) -> tuple[list[str], list[str], list[str]]:
    hard, soft, junk = [], [], []
    for w in words:
        if w in _CONV_STEMS or _stem(w) in _CONV_STEMS or w in ENTRY_WORDS:
            junk.append(w)
        elif w in tax.topic_stems:
            hard.append(w)
        elif w in tax.content_stems:
            soft.append(w)
        else:
            junk.append(w)
    return hard, soft, junk


def _detect_degree(text: str) -> str | None:
    for rx, deg in DEGREE_PATTERNS:
        if re.search(rx, text, re.I):
            return deg
    return None


def _all_degrees(text: str) -> list[str]:
    out: list[str] = []
    for rx, deg in DEGREE_PATTERNS:
        if re.search(rx, text, re.I) and deg not in out:
            out.append(deg)
    return out


def _enrich(info: Interpretation, corrected: str) -> Interpretation:
    """Derive the routing metadata (scope, mode, what to retrieve) from the validated intent: shared by the local and the AI interpreter."""
    t = corrected
    info.explicit_browse = bool(BROWSE_RX.search(t)) and (not OVERVIEW_RX.search(t) or bool(re.search(r"\b(programmes?|programs?|courses?|options)\b", t, re.I)))
    if info.intent == "comparison":
        info.compare_degrees = info.compare_degrees or _all_degrees(t)[:3]
    if info.intent == "concept_question":
        info.concept_terms = info.concept_terms or [x.key for x in glossary_terms(t)][:2]
    if info.intent == "general_conversation" and not info.converse_kind:
        info.converse_kind = conv.converse_kind(t) or "confused"
    if info.intent in ("programme_count",):
        info.scope, info.response_mode = "university", "statistics"
    elif info.intent in ("concept_question", "general_conversation", "greeting", "smalltalk", "unsupported"):
        info.scope = "none" if info.intent != "concept_question" else "university"
    elif info.intent == "comparison":
        info.scope, info.response_mode, info.requires_programme_search = "degree", "comparison_table", True
    elif info.intent == "clarification_needed":
        info.response_mode = "clarification"
    elif info.intent in ("programme_search",):
        info.scope, info.requires_programme_search = ("degree" if info.has_filter else "none"), True
        info.response_mode = "list" if info.explicit_browse else "conversational"
    elif info.intent in ("programme_overview", "availability_check"):
        info.scope, info.requires_programme_search = "degree", True
    elif info.intent in ("admission_process", "lateral_entry"):
        info.scope, info.requires_programme_search = ("degree" if info.has_filter else "university"), True
        info.requested_information = info.requested_information or ["admission_requirements", "eligibility", "application_process"]
    elif info.intent == "facilities":
        info.scope = "university"
    elif info.intent in ("programme_info", "campus_info", "scholarship"):
        info.scope = "programme" if not info.has_filter else "degree"
        info.requested_information = info.requested_information or ([info.requested_info] if info.requested_info else [])
    return info


def local_interpret(text: str, cat: Catalog | None, tax: Taxonomy, *, pending: bool = False) -> Interpretation:
    norm, allcaps = normalise_case(text.strip())
    corrected, changes = spell_correct(norm, tax)
    corrected_out = re.sub(r"\s+", " ", corrected).strip()
    base: dict[str, Any] = dict(original_message=text, corrected_message=corrected_out, corrections=changes, source="local")
    kind = smalltalk_kind(corrected) or smalltalk_kind(norm)
    if kind:
        return Interpretation(**base, intent="greeting" if kind == "greeting" else "smalltalk", smalltalk=kind, confidence=0.98)
    m = re.fullmatch(r"\s*(?:option|number|no\.?|#)?\s*#?\s*(\d{1,3})\s*[.!]?\s*", corrected)
    if m and pending:
        return Interpretation(**base, intent="selection", selection=int(m.group(1)), confidence=0.98)
    q = parse_query(corrected, cat)
    ck = conv.converse_kind(corrected)
    degrees = _all_degrees(corrected)
    if ck:                                                       # how are you / I'm fine / just exploring / who are you ...
        return _enrich(Interpretation(**base, intent="general_conversation", converse_kind=ck, confidence=0.95), corrected_out)
    if re.search(r"\b(confused|unsure|can'?t decide)\b", corrected, re.I) and re.search(r"\b(between|vs|versus|and)\b", corrected, re.I) and len(degrees) >= 2:
        return _enrich(Interpretation(**base, intent="general_conversation", converse_kind="confused_between", compare_degrees=degrees[:2], confidence=0.9), corrected_out)
    entry: str | None = "regular" if _REGULAR_RX.search(corrected) else ("lateral" if _LATERAL_RX.search(corrected) else None)
    fact = next((n for n, rx in FACT_PATTERNS if rx.search(corrected)), None)
    hard, soft, junk = _classify_words(q.words, tax)
    junk = [w for w in junk if w not in _CONV_STEMS and _stem(w) not in _CONV_STEMS and w not in ENTRY_WORDS]
    alias = next((a for a in re.findall(r"[a-z]+", corrected.lower()) if a in AMBIGUOUS_ALIAS), None)
    info = Interpretation(**base, intent="other", requested_info=fact, degree_type=q.degree, academic_level=q.level, concepts=[c.name for c in q.concepts], hard_words=hard, soft_words=soft,
                          campus=q.campus or _city_of(corrected, tax), entry_type=entry, irrelevant_tokens=junk, reset=q.reset, browse=q.browse, keep=q.keep)
    if info.entry_type == "regular" and not _REGULAR_RX.search(corrected):
        info.entry_type = None
    listy = bool(BROWSE_RX.search(corrected))
    terms = glossary_terms(corrected)
    # ---- intent: general rules over the structure of the sentence, most specific first
    if STATS_RX.search(corrected):
        info.intent = "programme_count"
    elif COMPARE_RX.search(corrected) and len(degrees) >= 2:
        info.intent = "comparison"
    elif is_facility_question(corrected, bool(info.degree_type or info.concepts or info.hard_words)) and not ADMISSION_RX.search(corrected):
        info.intent = "facilities"
        info.requested_info = None
        info.irrelevant_tokens = []
    elif CONCEPT_RX.search(corrected) and terms and not fact and not listy and not ADMISSION_RX.search(corrected) and not (entry == "lateral" and re.search(r"\b(available|offer\w*|have|in|for)\b", corrected, re.I) and len(degrees) + len(info.concepts) > 0 and "difference" not in corrected.lower()):
        info.intent = "concept_question"
    elif CONCEPT_RX.search(corrected) and not terms and not fact and not listy and (info.concepts or info.hard_words) and not info.degree_type and not ADMISSION_RX.search(corrected):
        info.intent = "programme_overview"                    # "explain Computer Science": what CHRIST offers in that area, in prose, not a list
    elif entry == "lateral":
        info.intent = "lateral_entry"
    elif fact == "apply" or ADMISSION_RX.search(corrected):
        info.intent = "admission_process"
        info.requested_info = "apply"
    elif fact and not (fact == "details" and info.has_filter):
        info.intent = "programme_info"
    elif CAMPUS_RX.search(corrected) and not listy:
        info.intent = "campus_info"
    elif SCHOLAR_RX.search(corrected):
        info.intent = "scholarship"
    elif (fact == "details" or OVERVIEW_RX.search(corrected)) and info.has_filter and not listy:
        info.intent = "programme_overview"
        info.requested_info = None
    elif AVAIL_RX.search(corrected) and info.has_filter and not (listy and re.search(r"\b(show|list)\b", corrected, re.I)):
        info.intent = "availability_check"
    elif OTHER_TOPIC_RX.search(corrected) and not (info.degree_type or info.academic_level or info.concepts or info.hard_words):
        info.intent = "other"                                # hostel / contact / ...: handled by the data tools, not a programme search
    elif info.has_filter or soft:
        info.intent = "programme_search"
    if info.intent in ("programme_search", "other") and not info.has_filter and alias and not fact:
        info.intent = "clarification_needed"
        info.clarification = f"Do you mean {AMBIGUOUS_ALIAS[alias]} (engineering) programmes, or technology-related programmes in another degree?"
        info.clarification_options = [f"{AMBIGUOUS_ALIAS[alias]} programmes", "Computer Science", "Show all engineering programmes"]
    if info.intent in ("other", "programme_search") and conv.looks_unrelated(corrected) and not (info.degree_type or info.academic_level or info.concepts or info.hard_words):
        info.intent = "unsupported"
    if info.intent == "programme_info" and fact == "details":
        info.requested_info = "details"
    info.confidence = max(0.3, 0.92 - 0.12 * len(junk) - (0.3 if info.intent == "other" else 0) - (0.1 if info.intent == "clarification_needed" else 0) - min(0.1, 0.03 * len(changes)))
    return _enrich(info, corrected_out)


def _city_of(text: str, tax: Taxonomy) -> str | None:
    t = text.lower()
    for city in ("bangalore", "bengaluru"):
        if re.search(rf"\b{city}\b", t):
            return "Bangalore"
    return None


# ------------------------------------------------------------------------------------------------ AI interpreter
class LLMOut(BaseModel):
    """The JSON the model must return. Anything else is rejected."""
    corrected_message: str = Field(default="", max_length=600)
    intent: str
    requested_info: str | None = None
    degree_type: str | None = None
    academic_level: str | None = None
    discipline: str | None = None
    specialization: str | None = None
    campus: str | None = None
    entry_type: str | None = None
    search_keywords: list[str] = Field(default_factory=list, max_length=12)
    irrelevant_tokens: list[str] = Field(default_factory=list, max_length=20)
    refers_to_active_programme: bool = False
    requested_information: list[str] = Field(default_factory=list, max_length=6)
    requires_programme_search: bool | None = None
    compare_degrees: list[str] = Field(default_factory=list, max_length=3)
    concept: str | None = Field(default=None, max_length=60)
    clarification: str | None = Field(default=None, max_length=300)
    confidence: float = Field(default=0.7, ge=0, le=1)

    @field_validator("intent")
    @classmethod
    def _intent(cls, v: str) -> str:
        if v not in INTENTS:
            raise ValueError("unknown intent")
        return v


SYSTEM = """You are the language-understanding module of a university admissions assistant for CHRIST (Deemed to be University).
Read the applicant's latest message (spelling mistakes, abbreviations, voice-to-text errors, ALL CAPS and short follow-ups are normal) and reply with ONE JSON object and nothing else:
{"corrected_message": "<same meaning, spelling fixed>",
 "intent": one of [greeting, smalltalk, general_conversation, programme_count, concept_question, comparison, programme_overview, programme_search, availability_check, programme_info,
                   admission_process, lateral_entry, campus_info, facilities (hostel, dining, library, transport, medical, labs, Wi-Fi), scholarship, selection, clarification_needed, unsupported, other],
 "requested_info": one of [eligibility, fees, curriculum, dates, selection, placements, apply, details] or null,
 "requested_information": ["<what the user wants to know, e.g. eligibility, application_process>"],
 "requires_programme_search": true|false,
 "degree_type": one of the DEGREES list or null, "academic_level": "Undergraduate"|"Postgraduate"|"Doctoral"|null,
 "discipline": "<subject area>" or null, "specialization": "<specialization>" or null, "campus": "<campus or city>" or null,
 "entry_type": "lateral"|"regular"|null, "compare_degrees": ["<degrees being compared>"], "concept": "<term to explain>" or null,
 "search_keywords": ["<meaningful subject words only>"], "irrelevant_tokens": ["<filler or misspelt filler words>"],
 "refers_to_active_programme": true|false, "clarification": "<ONE short question>" or null, "confidence": 0.0-1.0}
Rules: (1) Choose the intent by what the user WANTS, not by the words they use: a greeting, thanks, "how are you", "I'm fine" or "just exploring" never needs a programme search.
(2) programme_count = how many programmes/campuses; concept_question = what a term means or how two degrees differ in general; programme_overview = "tell me about X";
programme_search = the user wants to SEE or browse options; availability_check = "do you have / is X available"; admission_process = how to apply or get into something.
(3) Never put filler words (want, learn, study, tell, show, like...) in search_keywords, and never turn a spelling mistake into a specialization. (4) Keep negations and restrictions (only, without, not).
(5) "lateral entry", "direct second year", "diploma holders joining" mean entry_type "lateral". (6) Use the conversation state only to resolve references such as "this course" or "it".
(7) degree_type must be exactly one of DEGREES; B.E is not B.Tech. (8) If the message is ambiguous, intent clarification_needed with ONE short question. (9) Only interpret; never answer."""


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


class Runner:
    """Asks the interpreter model(s) for JSON. Providers are tried in order; invalid output is retried once with the error, then abandoned."""

    def __init__(self, providers: list, timeout_s: float = 7.0):
        self.providers, self.timeout_s = providers, timeout_s

    async def _ask(self, provider, messages) -> str | None:
        r = await asyncio.wait_for(provider.complete(messages, None, 0.0), timeout=self.timeout_s)
        return r.content

    async def ask_json(self, payload: dict) -> LLMOut | None:
        from ..llm.base import Message
        base = [Message("system", SYSTEM), Message("user", json.dumps(payload, ensure_ascii=False))]
        for provider in self.providers:
            msgs = list(base)
            for attempt in range(2):
                try:
                    raw = await self._ask(provider, msgs)
                except Exception as e:  # noqa: BLE001  timeout, rate limit, network: next provider
                    log.info("interpreter %s failed: %s", getattr(provider, "model", "?"), type(e).__name__)
                    break
                try:
                    d = _extract_json(raw or "")
                    if d is None:
                        raise ValueError("not a JSON object")
                    return LLMOut.model_validate(d)
                except (ValueError, ValidationError) as e:
                    msgs = base + [Message("assistant", (raw or "")[:500]), Message("user", f"That was invalid ({str(e)[:160]}). Return ONLY the JSON object described.")]
        return None


_runner: Runner | None | bool = False        # False = not resolved yet


def set_interpreter(r: Runner | None | bool) -> None:
    """Tests inject a Runner (or None to disable the AI interpreter)."""
    global _runner
    _runner = r


def _get_runner() -> Runner | None:
    global _runner
    if _runner is False:
        from ..llm import get_interpreter_providers
        provs = get_interpreter_providers()
        _runner = Runner(provs) if provs else None
    return _runner or None


def _validated_from_llm(out: LLMOut, local: Interpretation, tax: Taxonomy, cat: Catalog | None) -> Interpretation:
    """Turn the model's JSON into a trustworthy Interpretation: validate every entity against the taxonomy, keep the user's literal degree / route / campus."""
    notes: list[str] = []
    degree = None
    if out.degree_type:
        degree = _detect_degree(out.degree_type) or (out.degree_type if out.degree_type in DEGREE_LEVEL else None)
        if degree is None:
            notes.append(f"unknown degree dropped: {out.degree_type}")
    lit = _detect_degree(local.corrected_message)          # the user's own words win over the model
    if lit and degree != lit:
        notes.append(f"degree overridden by the message: {degree} -> {lit}")
        degree = lit
    level = out.academic_level if out.academic_level in ("Undergraduate", "Postgraduate", "Doctoral") else None
    if degree:
        level = DEGREE_LEVEL[degree]
    phrase = " ".join(x for x in [out.discipline, out.specialization, *out.search_keywords] if x)
    q = parse_query(phrase, cat) if phrase else Query()
    hard, soft, junk = _classify_words(q.words, tax)
    for j in junk:
        notes.append(f"keyword dropped (not in the taxonomy): {j}")
    campus = None
    if out.campus:
        t = out.campus.lower()
        campus = next((c for c in tax.campuses if c.lower() == t or t in c.lower()), None) or ("Bangalore" if "banga" in t or "bengal" in t else None)
        if campus is None:
            notes.append(f"unknown campus dropped: {out.campus}")
    campus = local.campus or campus
    entry = out.entry_type if out.entry_type in ("lateral", "regular") else None
    if local.entry_type:
        entry = local.entry_type
    elif entry and not _LATERAL_RX.search(local.corrected_message) and not _REGULAR_RX.search(local.corrected_message):
        notes.append("entry route from the model not supported by the message: dropped")
        entry = None
    info_field = out.requested_info if out.requested_info in FACT_NAMES else None
    intent = out.intent
    if intent == "lateral_entry" and entry is None:
        entry = "lateral"
    corrected = out.corrected_message.strip() if 3 <= len(out.corrected_message.strip()) <= 2 * max(len(local.original_message), 30) else local.corrected_message
    cmp_degs = [d for d in (_detect_degree(x) for x in out.compare_degrees) if d][:3] or (_all_degrees(corrected) if intent == "comparison" else [])
    iv = Interpretation(original_message=local.original_message, corrected_message=corrected, intent=intent, requested_info=info_field, degree_type=degree, academic_level=level,
                        requested_information=[x[:40] for x in out.requested_information], compare_degrees=cmp_degs,
                        concept_terms=[t.key for t in glossary_terms(out.concept or "")][:2] if out.concept else [],
                        concepts=[c.name for c in q.concepts], hard_words=hard, soft_words=soft, campus=campus, entry_type=entry, refers_to_active_programme=out.refers_to_active_programme,
                        irrelevant_tokens=list(dict.fromkeys([*out.irrelevant_tokens, *junk]))[:20], corrections=local.corrections, reset=local.reset, browse=local.browse, keep=local.keep,
                        clarification=out.clarification, confidence=out.confidence, source="llm", notes=notes)
    if iv.intent == "programme_info" and not iv.requested_info:
        iv.intent = "programme_search" if iv.has_filter else local.intent
    if iv.intent == "programme_search" and not iv.has_filter and not iv.soft_words:
        iv.intent = local.intent if local.intent != "programme_search" else "other"
    if iv.intent == "clarification_needed" and not iv.clarification:
        iv.intent = local.intent
    if iv.intent == "comparison" and len(iv.compare_degrees) < 2:
        iv.intent = local.intent if local.intent != "comparison" else "programme_search"
    if iv.intent == "concept_question" and not (iv.concept_terms or glossary_terms(corrected)):
        iv.intent = local.intent
    return _enrich(iv, corrected)


def _state_summary(state, cat: Catalog | None) -> dict:
    if state is None:
        return {}
    d: dict[str, Any] = {"degree": state.degree, "topic": list(state.concepts) + list(state.words), "campus": state.campus, "entry": state.entry,
                         "has_pending_numbered_choice": bool(state.pending), "result_count": len(state.last_ids)}
    p = cat.programs.get(state.programme_id) if (cat and state.programme_id) else None
    d["programme_under_discussion"] = p.name if p else None
    return d


def interpret(text: str, history: list[dict], state, cat: Catalog | None, *, allow_llm: bool = True) -> Interpretation:
    """The single entry point: local interpretation first, then (when useful and available) the AI interpreter, validated and merged."""
    tax = taxonomy_for(cat)
    local = local_interpret(text, cat, tax, pending=bool(state and state.pending))
    mode = (os.getenv("UNDERSTANDING_MODE") or "auto").lower()
    runner = _get_runner() if (allow_llm and mode != "local") else None
    if runner is None or local.intent in ("greeting", "smalltalk", "selection", "programme_count") or (local.intent == "general_conversation" and local.confidence >= 0.9):
        return local
    if mode != "llm" and local.confidence >= 0.8 and local.intent not in ("other", "clarification_needed"):
        return local                                        # understood with high confidence: no model call needed
    payload = {"message": text, "recent_turns": [{"role": m["role"], "text": m["content"][:200]} for m in history[-5:-1]], "state": _state_summary(state, cat),
               "DEGREES": list(tax.degrees), "CAMPUSES": list(tax.campuses), "CONCEPTS": list(tax.concepts), "local_guess": {"corrected_message": local.corrected_message, "intent": local.intent}}
    try:
        out = asyncio.run(runner.ask_json(payload))
    except Exception as e:  # noqa: BLE001  never let the interpreter break the chat
        log.warning("AI interpreter error: %s", type(e).__name__)
        out = None
    if out is None:
        local.notes.append("AI interpreter unavailable or invalid: local interpretation used")
        return local
    try:
        return _validated_from_llm(out, local, tax, cat)
    except Exception as e:  # noqa: BLE001  corrupt structure: do not run a corrupt query
        log.warning("AI interpretation rejected: %s", e)
        local.notes.append("AI interpretation rejected: local interpretation used")
        return local
