"""Conversation orchestrator: understand the message, decide WHAT KIND of answer it needs, retrieve only what that answer needs, then talk.

    message -> understanding (spelling, intent, entities, scope; app/agent/understanding.py)
            -> session memory (filters, topic, selected programme, last results, pending numbered choice, answered/asked/suggested)
            -> response type:  small talk | general conversation | statistics | concept | comparison | overview | availability | discovery (list or prose) |
                               programme facts | admission | lateral entry | campus | scholarship | clarification
            -> targeted retrieval (tools / catalog / stored records; a full programme search only when the user wants to discover programmes)
            -> a direct answer in plain prose (tables only for comparisons and explicit list requests, cards only when browsing) -> memory updated

* Only validated entities become filters. A new degree replaces the old degree and topic; unrelated questions (statistics, definitions, greetings) never touch the active filters.
* The state is returned with every reply and kept by the browser (the server stores nothing), so chats cannot mix and a refresh cannot lose a pending question.
* Nothing is generated about CHRIST: counts, programme names, eligibility, fees and lateral entry are retrieved; what is not stored is said to be not stored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass

from ..catalog.clean import looks_like_role
from ..catalog.indexes import norm_name, tokens
from ..catalog.lateral import lateral_index
from ..catalog.models import Catalog, Program, SectionKind
from ..catalog.refresh import current_catalog, get_reader, refresh
from ..catalog.search import Group, Query, State, facets, find_programmes, group_for, make_group, merge, related
from ..models import SourceRef
from . import conversation as conv
from . import knowledge as K
from .orchestrator import AgentResult
from .smalltalk import ACKS, BYES, GREETING_SEQUENCE, THANKS, greeting_reply, pick, smalltalk_kind, words as _words  # noqa: F401  (re-exported)
from .understanding import FACT_PATTERNS, Interpretation, interpret, local_interpret, taxonomy_for

log = logging.getLogger("dialog")

GREETINGS = GREETING_SEQUENCE            # kept for older imports
_FACTS = FACT_PATTERNS
_INTENT = {"eligibility": "eligibility_criteria", "fees": "programme_fees", "dates": "dates", "curriculum": "curriculum", "selection": "entrance_exam",
           "placements": "placements", "apply": "application_help", "details": "programme_details"}
_PCT = re.compile(r"\d{2}(\.\d+)?\s?(%|percent|per cent)", re.I)
_MORE_RX = re.compile(r"^\s*(show|see|give|list)?\s*(me\s+)?(some\s+)?(more|the rest|next|others)(\s+(options|programmes?|programs?|results?|ones))?\s*[.!?]?\s*$", re.I)
_LISTISH_RX = re.compile(r"\b(list|table|each|all of them|every|breakdown|side by side)\b", re.I)
_SHOW_RX = re.compile(r"^\s*(please\s+)?(show|list|see|display)(\s+me)?(\s+all)?(\s+of)?\s+(them|those|these|the\s+(list|options|programmes?|programs?|results?)|it)\s*[.!?]*\s*$", re.I)
MAX_FEE_ROWS = 12
PAGE = 5                                                         # how many programmes are listed at a time


# ------------------------------------------------------------------------------------------------ programme helpers
def find_named(cat: Catalog, text: str) -> Group | None:
    """A programme named in full in the message (what a card button sends), e.g. 'Tell me about Bachelor of Technology in Civil Engineering'."""
    nt = norm_name(text)
    lateral = "lateralentry" in nt
    best: Program | None = None
    for p in cat.programs.values():
        if len(p.dedupe_key) >= 12 and p.dedupe_key in nt and (best is None or len(p.dedupe_key) > len(best.dedupe_key)):
            best = p
    if best is None:
        return None
    rows = [p for p in cat.programs.values() if p.dedupe_key == best.dedupe_key and (p.entry == "lateral") == lateral] or [p for p in cat.programs.values() if p.dedupe_key == best.dedupe_key]
    return make_group(rows)


def group_of(cat: Catalog, pid: int | None) -> Group | None:
    return group_for(cat, pid)


def _campus_names(cat: Catalog, ids: tuple[int, ...]) -> list[str]:
    return [cat.campuses[i].name for i in ids if i in cat.campuses]


def label_of(s: State) -> str:
    bits = [s.degree or (s.level and s.level.lower())] + (["lateral entry"] if s.entry == "lateral" else []) + [c for c in s.concepts] + list(s.words) + ([f"at {s.campus}"] if s.campus else [])
    return " ".join(str(b) for b in bits if b)


def _title(g: Group) -> str:
    p = g.rep
    return p.name + (" (Lateral Entry)" if p.entry == "lateral" and "lateral" not in p.name.lower() else "") + (" · lateral entry also offered" if g.lateral_also else "")


def _card(cat: Catalog, g: Group, matched: list[str], number: int | None = None) -> dict:
    p = g.rep
    dept = cat.departments.get(p.department_id) if p.department_id else None
    roles = [r for r in (s.text.split("\n")[0][:80] for s in p.sections.get(SectionKind.CAREERS, ())) if looks_like_role(r)][:3]
    return {"programme_id": p.id, "number": number, "name": _title(g), "level": p.academic_level.lower(), "department": dept.name if dept else None,
            "duration_years": p.duration_years, "campuses": _campus_names(cat, g.campus_ids), "matched_interests": matched[:4], "career_paths": roles}


def _ref(p: Program) -> SourceRef:
    return SourceRef(doc_id=f"course:{p.id}", title=f"CHRIST programme database — {p.name}", url=p.url or None)


def _dur(v) -> str:
    try:
        n = float(v)
        return f"{int(n) if n == int(n) else n} years" if n != 1 else "1 year"
    except (TypeError, ValueError):
        return "Not listed"


def _tool(name: str, args: dict, tools_used: list[str], profile=None):
    """Run a registered tool with validated arguments (the same registry the tool-calling agent uses)."""
    from ..tools import ToolContext, execute
    out = execute(name, json.dumps({k: v for k, v in args.items() if v is not None}), ToolContext(profile=profile))
    if name not in tools_used:
        tools_used.append(name)
    return out


def _result(text: str, s: State, follow: list[str] | None = None, intent: str = "answer", cards: list | None = None, sources: list | None = None, used: list[str] | None = None,
            skill: str = "discover", compose: bool = False) -> AgentResult:
    r = AgentResult(text, sources if sources is not None else [SourceRef(doc_id="catalogue", title="CHRIST programme database")], used or [], follow or [], intent=intent, agent="info",
                    cards=cards or [], skill=skill, state=s.as_dict())
    r.composable = compose
    return r


def _remember(s: State, key: str) -> None:
    s.answered = tuple(dict.fromkeys((*s.answered, key)))[-24:]


def _asked(s: State, key: str) -> bool:
    return key in s.asked


def _ask(s: State, key: str) -> None:
    s.asked = tuple(dict.fromkeys((*s.asked, key)))[-12:]


def _recently_said(history: list[dict], needle: str, n: int = 4) -> bool:
    return any(needle in m["content"] for m in [m for m in history if m["role"] == "assistant"][-n:])


# ------------------------------------------------------------------------------------------------ state
@dataclass
class Dialog:
    result: AgentResult | None
    state: State
    note: str = ""                      # compact state summary for the model path (the backend, not the model, decides which programmes exist)
    interpretation: Interpretation | None = None


def ensure_catalog() -> Catalog | None:
    """The live catalog. If it has not been built yet (a request arriving during start-up, or a start-up that failed), build it now."""
    c = current_catalog()
    if c is None and get_reader() is not None:
        try:
            refresh(force=True, who="first-request")
        except Exception as e:  # noqa: BLE001  database down: the model path reports it honestly
            log.warning("catalog build on first request failed: %s", e)
        c = current_catalog()
    return c


def _validated(s: State, cat: Catalog) -> State:
    """The browser's state is data, not truth: every id must exist in the catalog."""
    s.last_ids = tuple(i for i in s.last_ids if i in cat.programs)
    if s.programme_id not in cat.programs:
        s.programme_id = None
    if s.pending:
        opts = [o for o in s.pending["options"] if o["programme_id"] in cat.programs]
        s.pending = {**s.pending, "options": [{"number": i + 1, "programme_id": o["programme_id"]} for i, o in enumerate(opts)]} if opts else None
    return s


TOPIC_INTENTS = {"programme_search", "programme_overview", "availability_check", "programme_info", "admission_process", "lateral_entry", "campus_info", "scholarship"}


def _replay(history: list[dict], cat: Catalog | None) -> State:
    """No state from the client: rebuild it from the earlier user messages with the local interpreter (no model calls)."""
    tax = taxonomy_for(cat)
    s = State()
    for m in history:
        if m["role"] != "user":
            continue
        iv = local_interpret(m["content"], cat, tax)
        if iv.intent in TOPIC_INTENTS:
            s = merge(s, iv.to_query())
    return s


def _numbered(cat: Catalog, groups: list[Group], with_campus: bool = True, start: int = 1) -> str:
    out = []
    for i, g in enumerate(groups, start=start):
        camp = ", ".join(_campus_names(cat, g.campus_ids))
        out.append(f"{i}. {_title(g)}" + (f" — {camp}" if with_campus and camp else ""))
    return "\n".join(out)


def _present(cat: Catalog, s: State, groups: list[Group], requested: str | None, limit: int = MAX_FEE_ROWS) -> list[Group]:
    """Remember exactly what the applicant was shown: the result ids and the numbered options a reply like '2' will resolve against."""
    shown = groups[:limit]
    s.last_ids = tuple(g.rep.id for g in groups[:30])
    s.shown = len(shown)
    s.pending = ({"action": "select_programme", "requested": requested, "options": [{"number": i, "programme_id": g.rep.id} for i, g in enumerate(shown, start=1)]}
                 if len(shown) > 1 else None)
    return shown


def _distinct_groups(cat: Catalog, ids) -> list[Group]:
    seen, out = set(), []
    for i in ids:
        g = group_of(cat, i)
        if g and g.rep.dedupe_key not in seen:
            seen.add(g.rep.dedupe_key)
            out.append(g)
    return out


# ------------------------------------------------------------------------------------------------ selection ("2", "the first one", a name)
_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
        "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9, "tenth": 10, "10th": 10}
_NUM_RX = re.compile(r"(?:option|number|no\.?|choice|programme|program|course|#)?\s*#?\s*(\d{1,3})(?:st|nd|rd|th)?(?:\s+(?:one|please|pls|programme|program|option|course))?")
_SEL_NOISE = {"want", "like", "programme", "program", "course", "option", "select", "choose", "pick", "one", "please", "tell", "about", "fees", "fee", "eligibility",
              "need", "show", "me", "give", "take", "go", "with", "for", "details", "info", "the", "that", "this", "degree", "specialization", "bachelor", "technology"}


def resolve_selection(cat: Catalog, s: State, text: str) -> tuple[str, object] | None:
    """('chosen', programme_id) | ('invalid', number) | None (not a selection). Only called while a question is pending."""
    opts = s.pending["options"] if s.pending else []
    if not opts:
        return None
    t = re.sub(r"[.!?,]+$", "", text.strip().lower())
    m = _NUM_RX.fullmatch(t)
    if m:
        n = int(m.group(1))
        return ("chosen", opts[n - 1]["programme_id"]) if 1 <= n <= len(opts) else ("invalid", n)
    ws = _words(t)
    if len(ws) <= 7:
        for w in ws:
            if w in _ORD:
                n = _ORD[w]
                return ("chosen", opts[n - 1]["programme_id"]) if n <= len(opts) else ("invalid", n)
        if "last" in ws and len(ws) <= 4:
            return ("chosen", opts[-1]["programme_id"])
    if len(ws) <= 10:
        want = [w for w in dict.fromkeys(tokens(t)) if w not in _SEL_NOISE]
        if want:
            cands = []
            for o in opts:
                p = cat.programs[o["programme_id"]]
                own = set(tokens(" ".join([p.name, p.field, p.specialization or ""])))
                if set(want) <= own:
                    base = own - {"bachelor", "technology", "master", "arts", "doctor", "philosophy"}
                    cands.append((o["number"], len(own - set(want)), base == set(want)))
            exact = [c for c in cands if c[2]]               # only the programme's FULL name is a choice; subject words alone ("Computer Science") narrow the search
            if len(exact) == 1:
                return ("chosen", opts[exact[0][0] - 1]["programme_id"])
    return None


def _options_groups(cat: Catalog, s: State) -> list[Group]:
    return [g for o in (s.pending["options"] if s.pending else []) if (g := group_of(cat, o["programme_id"]))]


def _selection_reply(cat: Catalog, history: list[dict], s: State, outcome: tuple[str, object], profile, last: str) -> AgentResult:
    kind, val = outcome
    pend = s.pending
    if kind == "invalid":
        groups = _options_groups(cat, s)
        k = len(groups)
        text = (f"**{val}** isn't one of the options. Please pick a number from 1 to {k} (or tell me the programme's name):\n\n" + _numbered(cat, groups))
        return _result(text, s, intent="selection_invalid", sources=[SourceRef(doc_id="catalogue", title="CHRIST programme database")])
    g = group_of(cat, int(val))
    requested = pend["requested"] if pend else None
    s.programme_id = g.rep.id
    s.topic = g.rep.name
    if requested:
        s.pending = None                                  # a clarification is resolved once: the question is not asked again
    if requested == "lateral":
        res = _lateral_detail(cat, s, g.rep.id, profile)
    elif requested == "apply":
        res = _admission_reply(cat, history, s, None, profile)
    elif requested in (None, "details"):
        res = _overview_reply(cat, history, s, None, profile, named=g)
    else:
        res = _answer_fact(cat, history, s, requested, g, profile, last, prefix=f"Taking **{g.rep.name}**:")
    s.last_intent = res.intent
    res.state = s.as_dict()
    return res


# ------------------------------------------------------------------------------------------------ main entry
def respond(history: list[dict], prior_state: dict | None = None, profile=None) -> Dialog:
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    kind = smalltalk_kind(last)
    if kind == "ack" and conv.asked_how_are_you(history):
        kind = None                                       # "fine" / "good" answers "how are you?"
    if kind:                                             # greetings, thanks, ok: no database call, no search, no loss of context
        cat0 = current_catalog()
        s0 = State.from_dict(prior_state) if prior_state else (_replay(history[:-1], cat0) if len(history) > 1 else State())
        if cat0 is not None:
            _validated(s0, cat0)
        iv0 = Interpretation(original_message=last, corrected_message=last, intent="greeting" if kind == "greeting" else "smalltalk", smalltalk=kind, confidence=0.98)
        return Dialog(_finalize(_smalltalk(kind, last, history, s0, cat0), s0), s0, interpretation=iv0)
    cat = ensure_catalog()
    if cat is None:
        return Dialog(None, State.from_dict(prior_state))
    prior = _validated(State.from_dict(prior_state), cat) if prior_state else None
    iv = interpret(last, history, prior, cat)           # the AI understanding step (spelling, intent, entities), validated against the taxonomy
    text = iv.corrected_message or last
    if iv.intent in ("greeting", "smalltalk") and iv.smalltalk:
        s0 = prior or _replay(history[:-1], cat)
        return Dialog(_finalize(_smalltalk(iv.smalltalk, text, history, s0, cat), s0), s0, interpretation=iv)
    if prior is not None and prior.pending:               # a numbered / named choice is resolved BEFORE any other intent
        outcome = resolve_selection(cat, prior, text)
        if outcome:
            res = _finalize(_selection_reply(cat, history, prior, outcome, profile, last), prior)
            return Dialog(res, prior, _note(cat, prior), iv)
    base = prior if prior is not None else _replay(history[:-1], cat)
    if _MORE_RX.search(last) and base.last_ids:           # "show more": continue the list the applicant was shown
        s = _validated(base, cat)
        res = _finalize(_browse_more(cat, history, s), s)
        return Dialog(res, s, _note(cat, s), iv)
    if _SHOW_RX.search(last) and base.last_ids and len(_distinct_groups(cat, base.last_ids)) > 1:       # "show them": list what we were just talking about
        s = _validated(base, cat)
        gs = _distinct_groups(cat, s.last_ids)
        res = _finalize(_browse_reply(cat, history, s, gs, s.topic or label_of(s) or "matching", []), s)
        return Dialog(res, s, _note(cat, s), iv)
    q = iv.to_query()
    # only topic questions change the filters: statistics, definitions, small talk and comparisons never leak into (or erase) the active search
    s = _validated(merge(base, q), cat) if iv.intent in TOPIC_INTENTS else _validated(base, cat)
    named = find_named(cat, text)
    if named is not None and iv.intent in TOPIC_INTENTS:
        s.programme_id = named.rep.id
    res: AgentResult | None = None
    fact = iv.requested_info
    intent = iv.intent
    if named is not None and intent == "programme_search" and not fact and next((rx for n, rx in FACT_PATTERNS if n == "details" and rx.search(text)), None):
        intent = "programme_overview"                    # "tell me about <a programme named in full>" is a question about that programme, not a search
    if intent == "clarification_needed" and iv.clarification:
        res = _result(iv.clarification, s, iv.clarification_options[:3] or [], intent="clarification")
    elif intent == "general_conversation":
        res = _converse(iv, history, s, cat)
    elif intent == "unsupported":
        t, f = conv.reply("unsupported", history)
        res = _result(t, s, f, intent="unsupported", sources=[])
    elif intent == "programme_count":
        res = _stats_reply(cat, history, s)
    elif intent == "concept_question":
        res = _concept_reply(cat, history, s, iv)
    elif intent == "comparison":
        res = _compare_reply(cat, history, s, iv)
    elif intent == "programme_overview":
        res = _overview_reply(cat, history, s, iv, profile, named)
    elif intent == "availability_check" and s.entry == "lateral":
        res = _lateral_reply(cat, history, s, iv, q, profile)                  # "is it available in B.Tech?" while discussing lateral entry
    elif intent == "availability_check":
        res = _availability_reply(cat, history, s, q, iv, profile)
    elif intent == "lateral_entry" or fact == "lateral":
        res = _lateral_reply(cat, history, s, iv, q, profile)
    elif intent == "admission_process":
        res = _admission_reply(cat, history, s, iv, profile)
    elif intent == "campus_info":
        res = _campus_reply(cat, history, s, q)
    elif intent == "scholarship":
        res = _scholarship_reply(cat, history, s, q, profile)
    elif intent == "programme_info" and fact:
        res = _fact(cat, history, s, q, fact, named, profile, text)
    elif intent == "programme_search" and (iv.has_filter or iv.soft_words or s.has_topic):
        res = _discovery_reply(cat, history, s, q, iv, profile)
    elif intent == "other" and os.getenv("AGENT_MODE", "").lower() != "tools" and not _OTHER_TOPIC(text):
        res = _result("I'm not sure I understood that. Could you tell me a little more? For example, you can ask about a programme, its eligibility or fees, lateral entry, or how to apply.", s,
                      [], intent="clarification", sources=[])
    if res is not None:
        s.last_intent = res.intent
        res = _finalize(res, s)
    return Dialog(res, s, _note(cat, s), iv)


def _OTHER_TOPIC(text: str) -> bool:
    from .understanding import OTHER_TOPIC_RX
    return bool(OTHER_TOPIC_RX.search(text))


def _finalize(res: AgentResult, s: State) -> AgentResult:
    """Quick replies only when they help: at most three, never the same ones twice in a row, no bare numbers (typing a number works anyway)."""
    fu = [f for f in res.follow_ups if f and not re.fullmatch(r"\d+", f) and f not in s.suggested][:3]
    res.follow_ups = fu
    s.suggested = tuple(fu)
    res.state = s.as_dict()
    return res


def _note(cat: Catalog, s: State) -> str:
    g = group_of(cat, s.programme_id)
    bits = []
    if s.has_topic:
        bits.append(f"Active search filters: {label_of(s)}.")
    if s.topic:
        bits.append(f"Current topic: {s.topic}.")
    if g:
        bits.append(f"Programme under discussion: {g.rep.name} (id {g.rep.id}).")
    if s.last_ids:
        bits.append("Last result ids: " + ", ".join(str(i) for i in s.last_ids[:10]) + ".")
    if s.answered:
        bits.append("Already explained: " + ", ".join(s.answered[-6:]) + ".")
    return " ".join(bits)


# ------------------------------------------------------------------------------------------------ small talk and general conversation
def _smalltalk(kind: str, text: str, history: list[dict], s: State, cat: Catalog | None) -> AgentResult:
    if kind == "ack" and conv.asked_how_are_you(history):
        t, f = conv.reply("doing_well", history)
        return _result(t, s, f, intent="smalltalk_ack", sources=[])
    reply = {"greeting": lambda: greeting_reply(text, history), "thanks": lambda: pick(THANKS, history), "ack": lambda: pick(ACKS, history), "bye": lambda: pick(BYES, history)}[kind]()
    follow: list[str] = []
    g = group_of(cat, s.programme_id) if cat is not None else None
    if kind == "greeting" and g is not None:
        reply += f"\n\nWe were looking at **{g.rep.name}**. Want its eligibility or curriculum?"
        follow = ["What is the eligibility?", "Show the curriculum"]
    elif kind == "greeting" and s.pending:
        reply += "\n\nYou were choosing a programme from the list above: reply with its number or name."
    elif kind == "greeting" and s.has_topic:
        reply += f"\n\nWe were on {label_of(s)}. Shall we pick up from there?"
    return _result(reply, s, follow, intent=f"smalltalk_{kind}", sources=[])


def _converse(iv: Interpretation, history: list[dict], s: State, cat: Catalog) -> AgentResult:
    kind = iv.converse_kind or "confused"
    if kind == "confused_between":
        gl = {t.degree: t for t in K.glossary().values() if t.degree}
        a, b = (gl.get(d) for d in iv.compare_degrees[:2])
        if a and b:
            same = a.level == b.level
            lead = f"Both are {a.level} options" if same else f"{a.name.split(' (')[0]} is {a.level} and {b.name.split(' (')[0]} is {b.level}"
            text = (f"{lead}, but they have different academic structures and focus areas. {a.name.split(' (')[0]} is {a.summary}, while {b.name.split(' (')[0]} is {b.summary}. "
                    f"The best choice depends on your interests and career goals.\n\nAre you mainly drawn to {a.focus}, or to {b.focus}?")
            return _result(text, s, [], intent="conversation", sources=[], compose=True)
        kind = "confused"
    t, f = conv.reply(kind, history)
    return _result(t, s, f, intent=f"conversation_{kind}", sources=[])


# ------------------------------------------------------------------------------------------------ statistics, concepts, comparison
def _stats_reply(cat: Catalog, history: list[dict], s: State) -> AgentResult:
    from ..catalogue import get_catalogue
    stats = K.programme_stats(cat, get_catalogue())
    text = K.stats_text(stats)
    if "stats" in s.answered and _recently_said(history, "programme listings", 6):
        text = f"As I mentioned, the database has {stats['listings']} programme listings ({stats['distinct']} distinct programmes). Let me know if you'd like a breakdown for a particular level."
    _remember(s, "stats")
    return _result(text, s, ["Show undergraduate programmes", "Show postgraduate programmes"], intent="programme_count", used=["get_university_statistics"], compose=True)


def _degree_facts(cat: Catalog, degree: str) -> str | None:
    gs = find_programmes(cat, Query(degree=degree))
    if not gs:
        return f"I can't find any {degree} programmes in the CHRIST records."
    camp = K.joined(list(dict.fromkeys(c for g in gs for c in _campus_names(cat, g.campus_ids)))[:3])
    areas = K.joined([f for f, _ in facets(gs, "field")[:3]])
    return f"At CHRIST there are {len(gs)} {degree} programmes" + (f", offered at {camp}" if camp else "") + (f", in areas such as {areas}" if areas and len(gs) > 3 else "") + "."


def _concept_reply(cat: Catalog, history: list[dict], s: State, iv: Interpretation) -> AgentResult:
    gl = K.glossary()
    terms = [gl[k] for k in iv.concept_terms if k in gl] or K.glossary_terms(iv.corrected_message)[:2]
    if not terms:
        return _result("I'm not sure which term you'd like explained. Could you say it another way?", s, [], intent="clarification", sources=[])
    chris: dict[str, str] = {}
    follow: list[str] = []
    for t in terms:
        if t.key == "lateral_entry":
            idx = lateral_index(cat)
            degs = sorted({e.degree_type for e in idx.values()})
            chris[t.key] = (f"At CHRIST, lateral entry is listed for {len(idx)} programmes" + (f" (all {degs[0]})" if len(degs) == 1 else "") + ". Whether you can use it depends on the eligibility published for the specific programme."
                            if idx else "I can't find lateral entry listed for any CHRIST programme in the database.")
            follow = ["Is it available in B.Tech?", "Which programmes offer it?"]
        elif t.degree:
            chris[t.key] = _degree_facts(cat, t.degree) or ""
        elif t.level:
            n = sum(1 for p in cat.programs.values() if p.academic_level.lower() == t.level)
            chris[t.key] = f"CHRIST has {n} {t.level} programmes in its records."
    if any(t.key == "lateral_entry" for t in terms):
        s.entry, s.topic = "lateral", "lateral entry"        # so "is it available in B.Tech?" and "what is the eligibility?" mean the lateral-entry route
    key = "concept:" + "+".join(t.key for t in terms)
    text = K.concept_text(terms, chris)
    if key in s.answered and _recently_said(history, terms[0].name):
        text = f"As I explained above: {terms[0].name} " + (terms[0].summary if terms[0].summary.startswith("means") else "is " + terms[0].summary) + ". Is there a particular part you'd like me to go into?"
        follow = []
    _remember(s, key)
    return _result(text, s, follow, intent="concept_question", used=["get_glossary_term"], compose=True)


def _compare_reply(cat: Catalog, history: list[dict], s: State, iv: Interpretation) -> AgentResult:
    q = iv.to_query()
    per: dict[str, list[Group]] = {}
    for d in iv.compare_degrees[:3]:
        per[d] = find_programmes(cat, Query(degree=d, concepts=q.concepts, words=q.words, campus=q.campus))
    topic = " ".join([c for c in iv.concepts] + list(iv.hard_words))
    if not any(per.values()):
        return _result(f"I can't find {K.joined(list(per))} programmes{' in ' + topic if topic else ''} in the CHRIST records, so I can't compare them.", s, [], intent="comparison", used=["search_programs"])
    table = K.compare_table(cat, per, topic)
    lead = f"Here's how {K.joined(list(per))}{' in ' + topic if topic else ''} compare at CHRIST:"
    text = f"{lead}\n\n{table}\n\nThe first row is the general idea of each degree; the rest comes from CHRIST's records. Exact criteria depend on the specific programme. Want the eligibility or curriculum for any of them?"
    srcs = [_ref(g[0].rep) for g in per.values() if g][:3]
    return _result(text, s, [], intent="comparison", sources=srcs or None, used=["search_programs"])


# ------------------------------------------------------------------------------------------------ overview, availability, discovery
def _search_groups(cat: Catalog, s: State, q: Query, profile=None, used: list[str] | None = None, limit_level: bool = True) -> list[Group]:
    used = used if used is not None else []
    args = {**_filter_args(s), "level": {"Undergraduate": "UG", "Postgraduate": "PG", "Doctoral": "PhD"}.get(s.level or "") if not s.degree else None,
            "entry_type": s.entry, "keywords": " ".join(q.soft) or None}
    out = _tool("search_programs", args, used, profile)
    ids = [p["programme_id"] for p in out.data.get("programmes", [])] if out.data.get("found") else []
    return [g for i in ids if (g := group_for(cat, i))]


def _overview_reply(cat: Catalog, history: list[dict], s: State, iv: Interpretation | None, profile, named: Group | None = None) -> AgentResult:
    used: list[str] = []
    groups = [named] if named is not None else _search_groups(cat, s, iv.to_query() if iv else Query(), profile, used)
    label = label_of(s) or "programme"
    if not groups:
        return _no_match(cat, history, s, iv.to_query() if iv else Query(), label, used)
    s.last_ids = tuple(g.rep.id for g in groups[:30])
    s.pending, s.shown = None, 0
    s.topic = label if len(groups) > 1 else groups[0].rep.name
    if len(groups) == 1:
        s.programme_id = groups[0].rep.id
    text = K.overview_text(cat, groups, label)
    follow: list[str] = []
    if len(groups) == 1:
        text += "\n\nI can go into its eligibility, curriculum or how to apply if that helps."
    elif not _asked(s, "overview_offer"):
        text += f"\n\nWould you like to know about the eligibility, fees or admission process for {label}?"
        _ask(s, "overview_offer")
        follow = ["What is the eligibility?", "How do I apply?"]
    _remember(s, "overview:" + s.topic)
    return _result(text, s, follow, intent="programme_overview", sources=[_ref(g.rep) for g in groups[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")], used=used or ["search_programs"],
                   cards=[], compose=True)


def _availability_reply(cat: Catalog, history: list[dict], s: State, q: Query, iv: Interpretation, profile) -> AgentResult:
    used: list[str] = []
    groups = _search_groups(cat, s, q, profile, used)
    label = label_of(s) or "that"
    unknown = iv.unknown_tokens() if iv else []
    if unknown and groups:                                  # a subject the records don't know can't be confirmed by the rest of the filters
        said = ", ".join(f"\u201c{t}\u201d" for t in unknown[:3])
        return _result(f"I can't find {said} among the {label} programmes in the CHRIST records, so I can't say it's offered.", s, [], intent="availability_check", used=used or ["search_programs"])
    text = K.availability_text(cat, groups, label)
    if groups:
        s.last_ids, s.pending, s.shown = tuple(g.rep.id for g in groups[:30]), None, 0
        s.topic = label
        if len(groups) == 1:
            s.programme_id = groups[0].rep.id
    follow = ["What is the eligibility?"] if groups else []
    return _result(text, s, follow, intent="availability_check", sources=[_ref(g.rep) for g in groups[:3]] or None, used=used or ["search_programs"], compose=True)


def _filter_args(s: State) -> dict:
    by_kind = {c.name: c.kind for c in s.concept_objs()}
    return {"degree_type": s.degree, "discipline": " ".join([n for n in s.concepts if by_kind.get(n) == "field"] + list(s.words)) or None,
            "specialization": " ".join(n for n in s.concepts if by_kind.get(n) == "spec") or None, "campus": s.campus}


def _discovery_reply(cat: Catalog, history: list[dict], s: State, q: Query, iv: Interpretation | None = None, profile=None) -> AgentResult:
    used: list[str] = []
    groups = _search_groups(cat, s, q, profile, used)
    label = label_of(s) or "matching"
    if not groups and (q.soft or (iv and iv.soft_words)) and not s.degree and not s.level and not s.concepts and not s.words:
        groups = related(cat, " ".join(q.soft or iv.soft_words))                                   # only soft keywords: rank by content, hard-filter nothing
        label = " ".join(q.soft or iv.soft_words)
    if not groups:
        s.last_ids, s.pending, s.programme_id, s.shown = (), None, None, 0
        return _no_match(cat, history, s, q, label, used)
    s.topic = label
    if iv is not None and iv.explicit_browse:
        return _browse_reply(cat, history, s, groups, label, used)
    return _conversational_discovery(cat, history, s, groups, label, used, iv)


def _tail_note(iv: Interpretation | None) -> str:
    unknown = iv.unknown_tokens() if iv else []
    return ("\n\n_I didn't recognise " + ", ".join(f"“{t}”" for t in unknown[:3]) + " as a subject or degree in the programme list, so I left it out of the search._") if unknown else ""


def _conversational_discovery(cat: Catalog, history: list[dict], s: State, groups: list[Group], label: str, used: list[str], iv: Interpretation | None) -> AgentResult:
    """The user is exploring, not asking for a list: answer in prose, remember the results, ask at most one useful question."""
    n = len(groups)
    s.last_ids, s.pending, s.shown = tuple(g.rep.id for g in groups[:30]), None, 0
    refs = [_ref(g.rep) for g in groups[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")]
    follow: list[str] = []
    if n == 1:
        g = groups[0]
        s.programme_id = g.rep.id
        p = g.rep
        camp = K.joined(_campus_names(cat, g.campus_ids))
        yrs = _dur(p.duration_years)
        text = (f"Yes, CHRIST has a {label} programme: **{p.name}**" + (f", offered at {camp}" if camp else "") + (f" ({yrs})" if yrs != "Not listed" else "") + "."
                + ("" if _asked(s, "details_offer") else " Would you like its eligibility, curriculum or admission process?"))
        _ask(s, "details_offer")
    elif n <= PAGE:
        s.programme_id = None
        names = K.joined([f"**{g.rep.name}**" + (" (lateral entry also offered)" if g.lateral_also else "") for g in groups])
        text = f"CHRIST has {n} {label} options: {names}."
        if not _asked(s, "details_offer"):
            text += " Would you like details on any of them?"
            _ask(s, "details_offer")
    else:
        s.programme_id = None
        text = K.overview_text(cat, groups, label)
        degs = facets(groups, "degree_type")
        areas = facets(groups, "field")
        if not s.degree and not s.level and len(degs) > 1 and not _asked(s, "degree"):
            text += "\n\nAre you looking at " + K.joined([d for d, _ in degs[:4]], "or") + "?"
            _ask(s, "degree")
            follow = [d for d, _ in degs[:3]]
        elif not s.concepts and not s.words and len(areas) > 1 and not _asked(s, "area"):
            text += "\n\nIs there an area you're leaning towards, or shall I tell you more about any of these?"
            _ask(s, "area")
        else:
            text += "\n\nTell me which one interests you and I can go into the details, or say “show them” to see the list."
    if s.entry == "lateral":
        text += "\n\n_Lateral entry (direct second-year admission) is shown only for programmes that list it._"
    _remember(s, "discovery:" + label)
    return _result(text + _tail_note(iv), s, follow, intent="programme_search", sources=refs, used=used or ["search_programs"], compose=True)


def _browse_reply(cat: Catalog, history: list[dict], s: State, groups: list[Group], label: str, used: list[str]) -> AgentResult:
    """The applicant asked to SEE the options: a short introduction, a few numbered programmes with cards, and an offer to show more."""
    n = len(groups)
    shown = _present(cat, s, groups, None, limit=PAGE)
    s.programme_id = None
    intro = f"Here {'is the' if n == 1 else 'are some of the'} {label} programme{'s' if n != 1 else ''} at CHRIST" + (f" ({n} in total)" if n > PAGE else "") + ":"
    text = intro + "\n\n" + _numbered(cat, shown) + (f"\n\nThere {'are' if n - len(shown) != 1 else 'is'} {n - len(shown)} more. Say “show more” to see them, or tell me which one you'd like to know about." if n > len(shown)
                                                      else "\n\nTell me which one you'd like to know more about.")
    if s.entry == "lateral":
        text += "\n\n_Lateral entry (direct second-year admission) is shown only for programmes that list it._"
    cards = [_card(cat, g, [c for c in s.concepts] + list(s.words), i) for i, g in enumerate(shown, start=1)]
    _remember(s, "list:" + label)
    return _result(text, s, [], intent="programme_search", cards=cards, sources=[_ref(g.rep) for g in shown[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")], used=used or ["search_programs"])


def _browse_more(cat: Catalog, history: list[dict], s: State) -> AgentResult:
    groups = _distinct_groups(cat, s.last_ids)
    start = s.shown
    batch = groups[start:start + PAGE]
    if not batch:
        return _result("That was the whole list. Tell me which programme you'd like to know more about, or ask me to narrow it down by specialization or campus.", s, [], intent="programme_search")
    s.shown = start + len(batch)
    opts = list(s.pending["options"]) if s.pending else []
    opts += [{"number": start + i, "programme_id": g.rep.id} for i, g in enumerate(batch, start=1)]
    s.pending = {"action": "select_programme", "requested": None, "options": opts[:MAX_FEE_ROWS * 2]}
    left = len(groups) - s.shown
    text = "Here are the next ones:\n\n" + _numbered(cat, batch, start=start + 1) + (f"\n\n{left} more are left. Say “show more” to continue." if left else "\n\nThat's the full list.")
    return _result(text, s, [], intent="programme_search", cards=[_card(cat, g, [], start + i) for i, g in enumerate(batch, start=1)], used=["search_programs"])


def _no_match(cat: Catalog, history: list[dict], s: State, q: Query, label: str, used: list[str] | None = None) -> AgentResult:
    follow: list[str] = []
    notes = []
    if s.degree == "B.E":
        tech = find_programmes(cat, State(degree="B.Tech", level="Undergraduate", concepts=s.concepts, words=s.words, campus=s.campus).query())
        notes.append("I couldn't find a matching **B.E** programme in the current CHRIST University programme listings." +
                     (f" It does list {len(tech)} **B.Tech** programme{'s' if len(tech) != 1 else ''} in this area, which is a different degree. Would you like to hear about them?" if tech else ""))
        if tech:
            follow.append("Tell me about B.Tech" + (f" {' '.join(s.concepts + s.words)}" if s.concepts or s.words else ""))
    else:
        notes.append(f"I couldn't find a matching **{label}** programme in the current CHRIST University programme listings.")
        if s.entry == "lateral":
            notes.append("Lateral entry is listed only for some programmes. I can tell you which ones.")
            follow.append("Which programmes offer lateral entry?")
        if s.degree and (s.concepts or s.words):
            others = find_programmes(cat, State(level=s.level, concepts=s.concepts, words=s.words, campus=s.campus).query())
            alt = [d for d, _ in facets(others, "degree_type") if d != s.degree][:3]
            if alt:
                notes.append("The same topic is available under: " + ", ".join(alt) + ".")
                follow += [f"Tell me about {d} {' '.join(s.concepts + s.words)}".strip() for d in alt]
        if not q.strict and not s.degree and not s.level:
            rel = related(cat, " ".join(list(s.concepts) + list(s.words)))
            if rel:
                notes.append("Closest related programmes:\n" + "\n".join(f"- {g.rep.name}" for g in rel[:5]))
    return _result("\n\n".join(notes), s, follow[:3], intent="no_match", used=used or ["search_programs"])


# ------------------------------------------------------------------------------------------------ facts about programmes
def _active_groups(cat: Catalog, s: State, q: Query) -> list[Group]:
    if not q.has_filter and s.last_ids:
        return _distinct_groups(cat, s.last_ids)
    return find_programmes(cat, s.query()) if s.has_topic else []


def _fact(cat: Catalog, history: list[dict], s: State, q: Query, fact: str, named: Group | None, profile, last: str) -> AgentResult | None:
    g = named
    groups: list[Group] = []
    if g is None and not q.has_filter:                    # a plain follow-up: the programme under discussion first
        g = group_of(cat, s.programme_id)
    if g is None:                                         # else the result list the applicant last saw (never a fresh unrestricted search)
        groups = _active_groups(cat, s, q)
        if len(groups) == 1:
            g = groups[0]
    if g is None:
        if not groups:
            return _result("Which programme do you mean? Tell me its name or a degree like B.Tech or MBA, and I'll look it up.", s, [], intent="clarification", sources=[])
        return _facts_across(cat, history, s, groups, fact, last)
    s.programme_id = g.rep.id
    s.topic = g.rep.name
    return _answer_fact(cat, history, s, fact, g, profile, last)


def _facts_across(cat: Catalog, history: list[dict], s: State, groups: list[Group], fact: str, last: str) -> AgentResult:
    """A question about several programmes at once (the MBA options, the B.Tech list): one direct answer instead of a menu."""
    label = s.topic or label_of(s) or "these"
    refs = [_ref(g.rep) for g in groups[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")]
    if fact == "fees":
        return _fees_overview(cat, history, s, groups, label, last)
    kinds = {"eligibility": (SectionKind.ELIGIBILITY, "eligibility criteria"), "selection": (SectionKind.SELECTION_PROCESS, "selection process"),
             "placements": (SectionKind.PLACEMENT, "placement information")}
    if fact in kinds:
        kind, what = kinds[fact]
        text = K.section_overview(groups, kind, label, what)
        if text is None and fact == "selection":
            text = K.section_overview(groups, SectionKind.ENTRANCE_TEST, label, "entrance test information")
        if text is None:
            text = f"I can't find published {what} for the {label} programmes in the database. The admissions office can confirm it."
        _remember(s, f"{fact}:{label}")
        return _result(text, s, [], intent={"eligibility": "eligibility_criteria", "selection": "entrance_exam", "placements": "placements"}[fact], sources=refs, used=["get_eligibility_criteria"] if fact == "eligibility" else ["get_programme_section"], compose=True)
    if fact == "dates":
        return _result(f"Application and entrance-test dates aren't published in the database I read, so I can't give you reliable dates for {label}. The admissions office can confirm the current schedule.\n\n{_contacts(None, cat)}",
                       s, [], intent="dates", sources=refs)
    shown = _present(cat, s, groups, fact, limit=PAGE)
    verb = {"curriculum": "the curriculum", "details": "the details", "apply": "how to apply"}.get(fact, "that")
    text = f"{verb.capitalize()} is published separately for each programme, so I need to know which one you mean. These are the {label} options:\n\n" + _numbered(cat, shown) + "\n\nTell me the number or name."
    return _result(text, s, [], intent="clarify", sources=refs)


def _contacts(campus_ids=None, cat: Catalog | None = None) -> str:
    """Admissions contacts, compact: one email when they all share it, phone numbers per campus (only the relevant campuses when known)."""
    from .. import live
    rows = live.admissions_contact().get("campuses", [])
    if campus_ids and cat is not None:
        names = {cat.campuses[i].name for i in campus_ids if i in cat.campuses}
        rows = [r for r in rows if r["campus"] in names] or rows
    if not rows:
        return "Please contact the Admissions Office of the campus you are applying to."
    emails = list(dict.fromkeys(r["email"] for r in rows if r["email"]))
    head = (f"You can reach admissions at {emails[0]}" if len(emails) == 1 else "You can reach admissions at " + K.joined(emails)) + ("; phone numbers:" if any(r["phones"] for r in rows) else ".")
    lines = [f"- {r['campus']}: {', '.join(r['phones'][:2])}" for r in rows if r["phones"]]
    return head + ("\n" + "\n".join(lines) if lines else "")


def _fees_overview(cat: Catalog, history: list[dict], s: State, groups: list[Group], label: str, last: str) -> AgentResult:
    from .. import live
    if _LISTISH_RX.search(last) and len(groups) <= MAX_FEE_ROWS:
        return _fees_list(cat, history, s, groups)             # the user explicitly asked for the list / table
    live.fee_records([i for g in groups for i in g.ids])      # one query for all
    got, missing = [], 0
    for g in groups:
        f = live.get_fees(g.rep.id)
        if f.available and f.value:
            got.append((g, f))
        else:
            missing += 1
    refs = [_ref(g.rep) for g in groups[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")]
    if not got:
        return _result(f"The fee structure isn't recorded for the {label} programmes in the database I read, so I can't quote fees and I won't guess any.\n\n{_contacts(None, cat)}", s, [], intent="programme_fees", sources=refs, used=["get_fees"])
    stale = any(f.value.get("validity_passed") for _, f in got)
    first_cat = (live.fee_year1(live.fee_for(got[0][0].rep.id)) or [(None, None)])[0][0]
    amounts: list[tuple[int, str]] = []
    for g, f in got:
        y1 = live.fee_year1(live.fee_for(g.rep.id))
        v = next((v for h, v in y1 if h == first_cat), None)
        num = re.sub(r"[^\d]", "", (v or "").split(" ")[0])
        if v and num:
            amounts.append((int(num), v))
    parts: list[str] = []
    if len(got) <= 3:
        lines = []
        for g, f in got:
            lines.append(f"- **{g.rep.name}**: {f.value['summary']}")
        parts.append(f"Here are the published fees for the {label} option{'s' if len(got) != 1 else ''}:\n\n" + "\n".join(lines))
    elif amounts and first_cat:
        lo, hi = min(amounts), max(amounts)
        parts.append(f"Fees are published per programme. For the {label} programmes that have a fee structure ({len(got)} of {len(groups)}), the year-1 fee for **{first_cat}** applicants runs from {lo[1]} to {hi[1]}. "
                     f"Other applicant categories (such as NRI and foreign nationals) have their own amounts.")
    else:
        parts.append(f"Fees are published per programme ({len(got)} of {len(groups)} {label} programmes have a fee structure).")
    if missing:
        parts.append(f"For {missing} programme{'s' if missing != 1 else ''} no fee structure is recorded.")
    parts.append(("These are the figures as stored in the CHRIST programme database" + ("; their stored validity date has passed, so please confirm the current fees with the admissions office." if stale else ".")) +
                 " Tell me which programme you mean and I'll show its full fee table by category.")
    _present(cat, s, [g for g, _ in got] + [g for g in groups if g not in [x for x, _ in got]], "fees", limit=PAGE)      # everything discussed stays the active result set
    _remember(s, f"fees:{label}")
    return _result("\n\n".join(parts), s, [], intent="programme_fees", sources=refs, used=["get_fees"], compose=True)


def _lateral_twin(cat: Catalog, g: Group) -> Group:
    """When the conversation is about lateral entry, facts about a programme come from its lateral listing if it has one."""
    if g.rep.entry == "lateral":
        return g
    twin = next((p for p in cat.programs.values() if p.dedupe_key == g.rep.dedupe_key and p.entry == "lateral"), None)
    return group_for(cat, twin.id) if twin else g


_LEAD = {"eligibility_criteria": "Here's the eligibility for **{n}**:", "curriculum": "Here's the curriculum for **{n}**:", "entrance_exam": "Here's how selection works for **{n}**:",
         "placements": "Here's what's published about placements for **{n}**:", "programme_details": "Here's an overview of **{n}**:"}


def _answer_fact(cat: Catalog, history: list[dict], s: State, fact: str, g: Group, profile, last: str, prefix: str = "") -> AgentResult:
    from ..catalogue import Resolution, get_catalogue
    from ..tools import ToolContext
    from .decision_engine import HANDLERS, NEXT, Req, Turn, Understanding, _finish
    if s.entry == "lateral":
        g = _lateral_twin(cat, g)
        s.programme_id = g.rep.id
        if fact == "eligibility":
            return _lateral_eligibility(cat, s, g, profile, prefix)
    if fact == "details":
        return _overview_reply(cat, history, s, None, profile, named=g)
    legacy = get_catalogue()
    prog = legacy.by_id.get(g.rep.id)
    if prog is None:
        return _result(f"{prefix}\n\nI can't open **{g.rep.name}** right now. Please try again.".strip(), s, [], intent="clarify", sources=[])
    cands = [legacy.by_id[i] for i in g.ids if i in legacy.by_id]
    intent = "eligibility_check" if fact == "eligibility" and _PCT.search(last) else _INTENT[fact]
    t = Turn(ToolContext(profile=profile))
    reply = HANDLERS[intent](t, Req(last, history, Understanding(intent, 1.0, None, None), Resolution(prog, [prog] + [c for c in cands if c is not prog], 1)))
    reply = _finish(reply)
    head, _, rest = reply.partition("\n\n")
    if head.startswith("### ") and intent in _LEAD:                # a plain sentence instead of a report heading; the campus / source line goes with it
        sub = ""
        if rest.startswith("~ "):
            sub, _, rest = rest.partition("\n\n")
        lead = _LEAD[intent].format(n=g.rep.name) + (f" _{sub[2:].replace('(as published in the CHRIST database)', '').strip(' ·')}_" if sub[2:].replace("(as published in the CHRIST database)", "").strip(" ·") else "")
        reply = f"{prefix}\n\n{lead}\n\n{rest}" if prefix else f"{lead}\n\n{rest}"
    elif prefix:
        reply = f"{head}\n\n{prefix}\n\n{rest}" if head.startswith("#") else f"{prefix}\n\n{reply}"
    _remember(s, f"{fact}:{g.rep.name}")
    follow = [x.format(n=g.rep.name) for x in NEXT[:3]] if False else []
    return _result(reply, s, follow, intent=intent, sources=list(t.sources.values()) or None, used=t.used, skill="program_qa")


def _lateral_eligibility(cat: Catalog, s: State, g: Group, profile, prefix: str) -> AgentResult:
    used: list[str] = []
    out = _tool("get_eligibility_criteria", {"programme_id": g.rep.id, "entry_type": "lateral"}, used, profile)
    d = out.data
    if d.get("found") is False or "sections" not in d:
        body = "I can't find lateral-entry eligibility published for this programme in the database. Please confirm with the Admissions Office."
    else:
        body = "\n\n".join(sec["text"] + (f"\n\n_{sec['section']}" + (f", {sec['academic_year']}" if sec.get("academic_year") else "") + "_") for sec in d["sections"][:2])
        body += "\n\n_Published eligibility only; it does not say whether a particular applicant qualifies. The Admissions Office decides._"
    text = (prefix + "\n\n" if prefix else "") + f"Here's the lateral-entry eligibility for **{g.rep.name}**:\n\n{body}"
    return _result(text, s, [], intent="eligibility_criteria", sources=list(out.sources), used=used, skill="eligibility")


def _which(cat: Catalog, s: State, found: list[Group], fact: str) -> AgentResult:
    verb = {"eligibility": "the eligibility", "fees": "the fees", "dates": "the dates", "curriculum": "the curriculum", "selection": "the selection process",
            "placements": "the placements", "apply": "how to apply", "details": "the details", "lateral": "the lateral entry details"}[fact]
    shown = _present(cat, s, found, fact, limit=PAGE)
    label = label_of(s) or "Your search"
    more = f"\n\n…and {len(found) - len(shown)} more. Tell me a specialization or campus to narrow it down." if len(found) > len(shown) else ""
    text = (f"**{label}** has {len(found)} matching programmes. Which one would you like {verb} for?\n\n" + _numbered(cat, shown) + more)
    return _result(text, s, [], intent="clarify")


def _fees_list(cat: Catalog, history: list[dict], s: State, groups: list[Group]) -> AgentResult:
    """Fees for the SAME programmes the applicant was just shown (asked for as a list): one row per programme and campus; a fee only if it is stored."""
    from .. import live
    shown = _present(cat, s, groups, "fees", limit=MAX_FEE_ROWS)
    rows = ["| # | Programme | Campus | Duration | Fees |", "|---|---|---|---|---|"]
    verified = 0
    stale = False
    for i, g in enumerate(shown, start=1):
        fact = live.get_fees(g.rep.id)
        ok = fact.available and bool(fact.value)
        verified += 1 if ok else 0
        stale = stale or bool(ok and fact.value.get("validity_passed"))
        cell = fact.value["summary"].replace("|", "/") if ok else "Fee information currently unavailable."
        for c in (_campus_names(cat, g.campus_ids) or ["Not listed"]):      # campus-specific fees stay separate rows
            rows.append(f"| {i} | {_title(g).replace('|', '/')} | {c} | {_dur(g.rep.duration_years)} | {cell} |")
    if verified:
        note = ("\n\nThese are the fee structures published in the CHRIST programme database, shown exactly as stored. "
                + ("Their stored validity date has passed, so please confirm the current fees with the admissions office. " if stale else "")
                + "Reply with a number for a programme's full fee table by applicant category."
                + ("\n\nWhere a row says unavailable, no fee structure is recorded for that programme." if verified < len(shown) else ""))
    else:
        note = f"\n\nNo verified fee structure is available for these programmes in the current university database, and I won't guess any.\n\n{_contacts(None, cat)}"
    more = f"\n\nThere are {len(groups) - len(shown)} more programmes in this search. Tell me a specialization or campus to narrow it down." if len(groups) > len(shown) else ""
    text = f"Here are the fees for the **{label_of(s) or 'matching'}** programmes you were just shown:\n\n" + "\n".join(rows) + more + note
    return _result(text, s, [], intent="fees_list", sources=[_ref(g.rep) for g in shown[:3]] + [SourceRef(doc_id="catalogue", title="CHRIST programme database")], used=["get_fees"], skill="fees_deadlines")


# ------------------------------------------------------------------------------------------------ campus and scholarships
def _campus_reply(cat: Catalog, history: list[dict], s: State, q: Query) -> AgentResult:
    g = group_of(cat, s.programme_id)
    groups = [g] if g else _active_groups(cat, s, q)
    if not groups:
        names = [c.name for c in sorted(cat.campuses.values(), key=lambda c: c.id)]
        return _result(f"CHRIST has {len(names)} campuses: {K.joined(names)}. Tell me a programme and I'll say where it is offered.", s, [], intent="campus_info", used=["get_campus_contacts"])
    label = s.topic or label_of(s) or "these programmes"
    if len(groups) == 1:
        camp = K.joined(_campus_names(cat, groups[0].campus_ids)) or "a campus that isn't recorded in the database"
        text = f"**{groups[0].rep.name}** is offered at {camp}."
    else:
        per: dict[str, int] = {}
        for gg in groups:
            for c in _campus_names(cat, gg.campus_ids):
                per[c] = per.get(c, 0) + 1
        parts = [f"{c} ({n} option{'s' if n != 1 else ''})" for c, n in sorted(per.items(), key=lambda x: -x[1])]
        text = f"The {label} options are offered at {K.joined(parts)}."
    return _result(text, s, [], intent="campus_info", sources=[_ref(gg.rep) for gg in groups[:3]] or None, used=["search_programs"], compose=True)


def _scholarship_reply(cat: Catalog, history: list[dict], s: State, q: Query, profile) -> AgentResult:
    g = group_of(cat, s.programme_id)
    groups = [g] if g else _active_groups(cat, s, q)
    label = s.topic or label_of(s) or "programme"
    text = K.section_overview(groups, SectionKind.SCHOLARSHIP, label, "scholarship information") if groups else None
    if text is None:
        text = (f"I don't have scholarship details for {label if groups else 'a particular programme'} in the database I read. Scholarships can differ by programme, so the admissions office can tell you what applies.\n\n{_contacts(None, cat)}")
    return _result(text, s, [], intent="scholarship", sources=[_ref(gg.rep) for gg in groups[:3]] or None, used=["get_programme_section"], compose=True)


# ------------------------------------------------------------------------------------------------ lateral entry
def _lateral_reply(cat: Catalog, history: list[dict], s: State, iv: Interpretation, q: Query, profile) -> AgentResult:
    """Lateral entry is an ADMISSION ROUTE, not a programme filter on every B.Tech: check the programme under discussion, else every programme matching the active filters."""
    s.entry = "lateral"
    used: list[str] = []
    new_filters = bool(q.degree or q.concepts or q.words or q.campus or q.level)
    pid = s.programme_id if (s.programme_id and not new_filters) else None
    args = {"programme_id": pid} if pid else _filter_args(s)
    out = _tool("search_lateral_entry", args, used, profile)
    d = out.data
    rows = d.get("programmes_offering_lateral_entry", [])
    label = d.get("checked_programme") or label_of(s)
    if pid:
        if rows:
            return _lateral_detail(cat, s, rows[0]["programme_id"], profile, out)
        others = _tool("search_lateral_entry", {}, used, profile).data.get("programmes_offering_lateral_entry", [])
        text = f"I can't find lateral entry listed for **{label}** in the database, so I can't say it is offered. Please confirm with the Admissions Office."
        if others:
            text += "\n\nLateral entry **is** listed for these programmes:\n\n" + "\n".join(f"{i}. {r['name']}" for i, r in enumerate(others[:PAGE], start=1))
            _present_rows(cat, s, others, "lateral")
        return _result(text, s, [], intent="lateral_entry", sources=list(out.sources), used=used, skill="apply_guide")
    if not rows:
        extra = d.get("matching_programmes_without_a_lateral_entry_listing") or []
        text = f"I couldn't find lateral entry listed for **{label or 'those programmes'}** in the database."
        if extra:
            text += " These matching programmes have no lateral-entry listing:\n\n" + "\n".join(f"- {n}" for n in extra[:6])
        text += "\n\nI can tell you which programmes do list it."
        s.last_ids, s.pending = (), None
        return _result(text, s, ["Which programmes offer lateral entry?"], intent="lateral_entry", sources=list(out.sources), used=used, skill="apply_guide")
    if len(rows) == 1:
        return _lateral_detail(cat, s, rows[0]["programme_id"], profile, out)
    shown = _present_rows(cat, s, rows, "lateral")
    scope = f"matching **{label}**" if label else "at CHRIST"
    text = (f"Let me check. Lateral entry is listed for {len(rows)} programmes {scope}:\n\n"
            + "\n".join(f"{i}. {r['name']}" + (f" — {', '.join(r['campuses'])}" if r["campuses"] else "") for i, r in enumerate(shown, start=1))
            + ("\n\nSay “show more” for the rest. " if len(rows) > len(shown) else "\n\n")
            + "Which one would you like the eligibility and admission details for?\n\n_Lateral entry is shown only where the database lists it for that programme; it is not assumed for other programmes of the same degree._")
    return _result(text, s, [], intent="lateral_entry", sources=list(out.sources), used=used, skill="apply_guide",
                   cards=[_card(cat, group_for(cat, r["programme_id"]), [], i) for i, r in enumerate(shown[:PAGE], start=1) if group_for(cat, r["programme_id"])])


def _present_rows(cat: Catalog, s: State, rows: list[dict], requested: str) -> list[dict]:
    shown = rows[:PAGE]
    s.last_ids = tuple(r["programme_id"] for r in rows[:30])
    s.shown = len(shown)
    s.pending = {"action": "select_programme", "requested": requested, "options": [{"number": i, "programme_id": r["programme_id"]} for i, r in enumerate(shown, start=1)]} if len(shown) > 1 else None
    return shown


def _lateral_detail(cat: Catalog, s: State, programme_id: int, profile, out=None) -> AgentResult:
    used: list[str] = []
    out = _tool("search_lateral_entry", {"programme_id": programme_id}, used, profile)
    rows = out.data.get("programmes_offering_lateral_entry", [])
    s.entry = "lateral"
    if not rows:
        g = group_for(cat, programme_id)
        return _result(f"I can't find lateral entry listed for **{g.rep.name if g else 'that programme'}** in the database.", s, ["Which programmes offer lateral entry?"], intent="lateral_entry",
                       sources=list(out.sources), used=used, skill="apply_guide")
    r = rows[0]
    s.programme_id = r["programme_id"]
    s.last_ids = (r["programme_id"],)
    s.topic = r["name"]
    how = {"listing": "a separate Lateral Entry listing", "eligibility_text": "a Lateral Entry paragraph in its published eligibility"}
    why = " and ".join(how[e] for e in r["evidence"] if e in how)
    lines = [f"Yes, lateral entry is listed for **{r['name']}**" + (f" ({', '.join(r['campuses'])})" if r["campuses"] else "") + f". The database has {why}."]
    if r["eligibility"]:
        lines += ["", "Who can apply, as published:", "", r["eligibility"]]
        if r["eligibility_source"]:
            lines += ["", f"_Source: {r['eligibility_source']}_"]
    if r["entry_year_or_semester"]:
        lines += ["", f"The published text puts the entry point at the **{r['entry_year_or_semester']}**."]
    if r["admission_process"]:
        lines += ["", "Admission procedure, as published:", "", r["admission_process"]]
    missing = r["not_stated_in_database"]
    if missing:
        lines += ["", "What the database does **not** state: " + ", ".join(missing) + ". Please confirm these with the Admissions Office; I won't guess them.", "", _contacts(None, cat)]
    g = group_for(cat, r["programme_id"])
    return _result("\n".join(lines), s, [], intent="lateral_entry", sources=list(out.sources), used=used, skill="apply_guide", cards=[_card(cat, g, [], 1)] if g else [])


# ------------------------------------------------------------------------------------------------ admission
def _admission_reply(cat: Catalog, history: list[dict], s: State, iv: Interpretation | None, profile) -> AgentResult:
    """How to get in: the eligibility and selection published for the programme(s) in question, plus the application steps if the database has them."""
    used: list[str] = []
    g = group_of(cat, s.programme_id)
    groups = [g] if g else (_active_groups(cat, s, iv.to_query() if iv else Query()) if (s.has_topic or s.last_ids) else [])
    label = (g.rep.name if g else (s.topic or label_of(s))) or "CHRIST"
    pid = g.rep.id if g else (groups[0].rep.id if len(groups) == 1 else None)
    out = _tool("search_admission_process", {"query": (iv.corrected_message if iv else None) or "how to apply", "programme_id": pid, "entry_type": "lateral" if s.entry == "lateral" else None}, used, profile)
    process = out.data if out.data.get("found") else None
    parts: list[str] = []
    refs = list(out.sources) if process else []
    if groups:
        _tool("get_eligibility_criteria", {"programme_id": groups[0].rep.id, "entry_type": "lateral" if s.entry == "lateral" else None}, used, profile)
        elig = K.section_overview(groups, SectionKind.ELIGIBILITY, label, "eligibility", limit=550, brief=True)
        sel = K.section_overview(groups, SectionKind.SELECTION_PROCESS, label, "selection process", limit=450, brief=True) or K.section_overview(groups, SectionKind.ENTRANCE_TEST, label, "entrance test details", limit=450, brief=True)
        n = len(groups)
        parts.append(f"Admission to {'the ' if n == 1 else ''}{label}{' programmes' if n > 1 and not g else ''} at CHRIST works through the published eligibility and selection process for the specific programme"
                     + (", so it can differ between the options." if n > 1 else "."))
        if elig:
            parts.append(elig)
        if sel:
            parts.append(sel)
        refs += [_ref(x.rep) for x in groups[:3]]
    if process:
        pp = process["passages"][0]
        parts.append(f"For the application steps, the published text says:\n\n{pp['text']}" + ("\n\n_The stored validity date of this text has passed: please confirm it with the Admissions Office._" if pp.get("expired_section") else ""))
    if not process:
        parts.append(("The step-by-step application procedure and dates aren't published in the database I read, so I won't guess them. " if groups else "I couldn't find a general application procedure in the database I read, so I won't guess one. "
                      "The procedure can differ by programme: name one and I'll check what is published for it. ") + "The Admissions Office can confirm the current steps.\n\n" + _contacts(None, cat))
    _remember(s, f"admission:{label}")
    return _result("\n\n".join(parts), s, [], intent="admission_process", sources=refs or None, used=used, skill="apply_guide", compose=False)


# ------------------------------------------------------------------------------------------------ idempotency
def request_key(client_msg_id: str | None, history: list[dict], conversation_id: str | None = None) -> str | None:
    """Same conversation + message id + length + text = the same submission (a double click or a retry), answered once."""
    if not client_msg_id:
        return None
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    return hashlib.sha1(f"{conversation_id or ''}|{client_msg_id}|{len(history)}|{last}".encode("utf-8")).hexdigest()
