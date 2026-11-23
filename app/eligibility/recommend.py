"""Recommendation over the catalog: hard filters first (eligibility, audience, campus, level), then soft ranking by interest (BM25).

Never suggests a programme the applicant is ineligible for, and never predicts admission chances or cutoffs (the data holds no
historical selection results). Programmes without a reviewed rule are suggested with their eligibility clearly marked unverified.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..catalog.clean import looks_like_role
from ..catalog.indexes import tokens
from ..catalog.models import Catalog, Program, SectionKind
from .checker import Profile, Verdict, check_eligibility
from .normalize import normalize_qualification

INTEREST_KINDS = {"overview", "careers", "career_roles", "what_you_learn", "why_choose", "modules", "specialization", "highlights", "objectives"}
_NEXT_LEVEL = {"10+2": ("UG", "Diploma", "Certificate"), "UG degree": ("PG", "Diploma", "Certificate"), "PG degree": ("PhD", "PG", "Diploma", "Certificate")}
def expand_interests(text: str) -> list[str]:
    """What the applicant likes, as tokens: chatter dropped, field words expanded ('aiml' -> artificial intelligence machine learning ...)."""
    from ..catalogue import INTEREST_SYN, QUESTION_WORDS, RECO_NOISE
    out: list[str] = []
    for t in tokens(text):
        if t in RECO_NOISE or t in QUESTION_WORDS:
            continue
        out.append(t)
        out.extend(INTEREST_SYN.get(t, []))
    return list(dict.fromkeys(out))


SHOULD_ASK = "Tell me what subjects or career you like, and whether you want an undergraduate, postgraduate or PhD programme."


def _campus_ids(cat: Catalog, name: str | None) -> set[int] | None:
    if not name:
        return None
    words = [w for w in name.lower().replace("campus", " ").split() if w not in ("bangalore", "bengaluru", "the", "christ")] or name.lower().split()
    ids = {c.id for c in cat.campuses.values() if any(w in c.name.lower() for w in words)}
    return ids or set()


def _levels(profile: Profile, level: str | None) -> tuple[str, ...] | None:
    if level or profile.level:
        return (level or profile.level,)
    q = normalize_qualification(profile.qualification)
    return _NEXT_LEVEL.get(q) if q else None


def rank_programs(cat: Catalog, profile: Profile, interests: str | None = None, *, level: str | None = None, campus: str | None = None,
                  limit: int = 5, exclude_names: list[str] | None = None) -> dict[str, Any]:
    """Ranked programmes (one entry per distinct programme, campuses listed together) with why they match and their eligibility result."""
    interest_text = interests or profile.interests or ""
    qtok = expand_interests(interest_text)
    if not qtok:
        return {"found": False, "message": SHOULD_ASK, "recommendations": []}
    levels = _levels(profile, level)
    campus_ids = _campus_ids(cat, campus or profile.preferred_campus)
    skip = {n.strip().lower() for n in (exclude_names or [])}

    # soft ranking: BM25 over interest-bearing chunks, summed per programme
    scores: dict[int, float] = defaultdict(float)
    why: dict[int, list[str]] = defaultdict(list)
    bm = cat.indexes.bm25
    if bm:
        pool = cat.indexes.chunks
        for i, sc in bm.search(qtok, 400, lambda i: pool[i].kind in INTEREST_KINDS and pool[i].program_id is not None):
            c = pool[i]
            scores[c.program_id] += sc
            if len(why[c.program_id]) < 2 and c.title not in why[c.program_id]:
                why[c.program_id].append(c.title.split(" - ", 1)[-1])
    # name matches count double (a programme called "Data Science" beats one that mentions it once)
    for pid, p in cat.programs.items():
        nt = set(tokens(p.name))
        hit = [t for t in qtok if t in nt]
        if hit:
            scores[pid] += 8.0 * len(hit)

    groups: dict[str, list[tuple[float, Program, Verdict]]] = defaultdict(list)
    for pid, sc in scores.items():
        p = cat.programs[pid]
        if levels and p.level not in levels:
            continue
        if campus_ids is not None and not ({p.campus_id} | {pl.campus_id for pl in p.placements}) & campus_ids:
            continue
        if p.name.strip().lower() in skip:
            continue
        v = check_eligibility(profile, p)
        if v.outcome == "not_eligible":                     # hard filter: never suggest an ineligible programme
            continue
        groups[" ".join(tokens(p.name))].append((sc + (3.0 if p.campus_id == 1 else 0.0) + (2.0 if p.quality.unreviewed_eligibility is False else 0.0), p, v))
    ranked = []
    for rows in groups.values():
        rows.sort(key=lambda r: (-r[0], r[1].id))
        sc, best, v = rows[0]
        ranked.append((sc, best, v, rows))
    ranked.sort(key=lambda r: (-r[0], r[1].name))
    out = []
    for sc, p, v, rows in ranked[:max(1, min(limit, 5))]:
        careers = [r for r in (s.text.split("\n")[0][:90] for s in p.sections.get(SectionKind.CAREERS, ())) if looks_like_role(r)][:3]
        hay = set(tokens(" ".join([p.name, p.overview[:600], " ".join(careers)] + [c.title for c in cat.indexes.chunks[:0]])))
        matched_words = [t for t in dict.fromkeys(qtok) if t in hay][:4]
        out.append({"programme_id": p.id, "name": p.name, "level": p.level, "department": cat.departments[p.department_id].name if p.department_id in cat.departments else None,
                    "duration_years": p.duration_years, "campuses": sorted({cat.campuses[r[1].campus_id].name for r in rows if r[1].campus_id in cat.campuses}),
                    "matched_on": why.get(p.id, [])[:2], "matched_words": matched_words or qtok[:2], "career_paths": careers, "url": p.url,
                    "eligibility": {"outcome": v.outcome, "verified": v.outcome in ("eligible", "need_info") and not p.quality.unreviewed_eligibility,
                                    "question": v.question, "note": ("Eligibility not verified: read the published criteria." if v.outcome == "cannot_decide" else None)}})
    if not out:
        return {"found": False, "message": "Nothing in the catalog matches those interests with your profile. Ask what subjects they like, or widen the level or campus.", "recommendations": []}
    return {"found": True, "recommendations": out,
            "note": "Suggestions only, matched on programme content. No admission chances or cutoffs are predicted; the admissions office decides eligibility."}


def compare_programs(cat: Catalog, ids: list[int]) -> dict[str, Any]:
    """Side-by-side facts for 2-4 programmes. Fees, dates and seats are never included (ERP only)."""
    rows = []
    for pid in ids[:4]:
        p = cat.programs.get(pid)
        if not p:
            continue
        sec = lambda k: (p.sections.get(k) or (None,))[0]
        elig, sel = sec(SectionKind.ELIGIBILITY), sec(SectionKind.SELECTION_PROCESS)
        rows.append({"programme_id": p.id, "name": p.name, "level": p.level, "campus": cat.campuses[p.campus_id].name if p.campus_id in cat.campuses else None,
                     "department": cat.departments[p.department_id].name if p.department_id in cat.departments else None, "duration_years": p.duration_years,
                     "term_type": p.term_type, "semesters": p.semesters, "specialisations": [s.name for s in p.specializations if s.name != "Programme roadmap"][:6],
                     "eligibility": (elig.text[:500] if elig else None), "selection": (sel.text[:400] if sel else None), "url": p.url,
                     "placement_highlights": list(p.placement_highlights)[:3]})
    return {"found": len(rows) >= 1, "programmes": rows}
