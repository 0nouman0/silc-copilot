"""Grounded knowledge for conversational answers: statistics, overviews, availability, campuses, eligibility across several programmes, comparisons, fee ranges.

Everything about CHRIST (counts, programme names, durations, campuses, eligibility text, fee figures) is read from the catalog / stored records at the moment of
the question; the only static text is the neutral glossary in config/glossary.yaml, which explains terms in general and never states a CHRIST fact.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import yaml

from ..catalog.config import CONFIG_DIR
from ..catalog.models import Audience, Catalog, Program, SectionKind
from ..catalog.search import DEGREE_LEVEL, Group, facets

# ------------------------------------------------------------------------------------------------ glossary
@dataclass(frozen=True)
class Term:
    key: str
    name: str
    summary: str
    degree: str | None
    level: str | None
    focus: str | None
    patterns: tuple[re.Pattern, ...]


@lru_cache(maxsize=1)
def glossary() -> dict[str, Term]:
    p = CONFIG_DIR / "glossary.yaml"
    raw = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("terms", []) if p.exists() else []
    return {t["key"]: Term(t["key"], t["name"], t["summary"], t.get("degree"), t.get("level"), t.get("focus"), tuple(re.compile(x, re.I) for x in t.get("patterns", []))) for t in raw}


def glossary_terms(text: str) -> list[Term]:
    """Terms named in the text, in the order they appear (a term is matched once)."""
    found: list[tuple[int, Term]] = []
    for t in glossary().values():
        pos = [m.start() for rx in t.patterns if (m := rx.search(text))]
        if pos:
            found.append((min(pos), t))
    out: list[Term] = []
    for _, t in sorted(found, key=lambda x: x[0]):
        out.append(t)
    return out


def degree_terms(text: str) -> list[Term]:
    return [t for t in glossary_terms(text) if t.degree]


# ------------------------------------------------------------------------------------------------ helpers
def first_sentences(text: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = list(re.finditer(r"[.!?](\s|$)", cut))
    return (cut[:m[-1].end()].strip() if m else cut.rsplit(" ", 1)[0] + "…")


def joined(items: list[str], conj: str = "and") -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else (", ".join(items[:-1]) + f" {conj} " + items[-1] if items else "")


def campus_names(cat: Catalog, ids) -> list[str]:
    return [cat.campuses[i].name for i in ids if i in cat.campuses]


def _yrs(v) -> str | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return f"{int(n) if n == int(n) else n}-year" if n > 0 else None


_NOISE_BITS = re.compile(r"honou?rs?(\s+with\s+research)?|recognis?ed by \w+|deemed to be university|\bcampus\b|integrated masters? programme|one year pg", re.I)


def variant_tags(p: Program) -> list[str]:
    """What distinguishes this listing from others of the same degree, read from its stored name: Executive, an international credit-exchange partner, or a specialization."""
    name = re.sub(r"\s+", " ", p.name)
    tags: list[str] = []
    if re.search(r"\bexecutive\b", name, re.I):
        tags.append("Executive")
    for m in re.finditer(r"(?i:credits? exchange with (?:the )?(?:[a-z]{2,6} of )?)([A-Z][^()+]*?)(?:\)|\(|$)", name):
        tags.append("exchange:" + m.group(1).strip(" ,"))
    for g in re.findall(r"\(([^()]*)\)", name):
        if re.fullmatch(r"[A-Za-z.]{2,6}", g.strip()) or re.search(r"executive|credits? exchange|lateral entry", g, re.I) or re.search(r"\bcampus\b", g, re.I):
            continue
        parts = [x.strip() for x in re.split(r"/", g) if x.strip() and not _NOISE_BITS.fullmatch(x.strip())]
        clean = re.sub(r"^specialis?ation in\s+", "", ", ".join(parts), flags=re.I)
        if clean:
            tags.append(clean[:80])
    return list(dict.fromkeys(tags))


def variant_summary(groups: list[Group]) -> str | None:
    tags = [variant_tags(g.rep) for g in groups]
    execs = any("Executive" in t for t in tags)
    partners = list(dict.fromkeys(x[9:] for t in tags for x in t if x.startswith("exchange:")))
    specs = list(dict.fromkeys(x for t in tags for x in t if x != "Executive" and not x.startswith("exchange:")))
    plain = any(not t for t in tags)
    bits = []
    if plain:
        bits.append("the standard programme")
    if execs:
        bits.append("an Executive option")
    if partners:
        bits.append("options with international credit exchange (" + joined(partners) + ")")
    if specs:
        bits.append("specialisations such as " + joined(specs[:4]))
    return ("They include " + joined(bits) + ".") if bits else None


# ------------------------------------------------------------------------------------------------ statistics
def programme_stats(cat: Catalog, legacy) -> dict:
    lv = legacy.by_level()
    distinct = Counter(p.academic_level for p in cat.programs.values())
    return {"listings": len(legacy.programmes), "distinct": len(cat.programs), "campuses": len(cat.campuses),
            "listings_by_level": {"Undergraduate": lv["undergraduate"], "Postgraduate": lv["postgraduate"], "Doctoral": lv["doctoral"], "Other": lv["other"]},
            "distinct_by_level": {k: distinct.get(k, 0) for k in ("Undergraduate", "Postgraduate", "Doctoral")}, "degree_types": Counter(p.degree_type for p in cat.programs.values()).most_common()}


def stats_text(s: dict) -> str:
    lines = [f"According to the current CHRIST programme database there are **{s['listings']} programme listings**. A programme offered at more than one campus is listed once per campus, "
             f"so these are {s['distinct']} distinct programmes.", "", "By level (listings):"]
    d = s["distinct_by_level"]
    for k, n in s["listings_by_level"].items():
        if n or k != "Other":
            lines.append(f"- {k}: {n}" + (f" ({d[k]} distinct programmes)" if k in d else ""))
    lines += ["", f"They are spread across {s['campuses']} campuses."]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ overview
def label_for(degree: str | None, concepts: list[str] | tuple = (), words: list[str] | tuple = ()) -> str:
    bits = ([degree] if degree else []) + list(concepts) + list(words)
    return " ".join(bits) if bits else "programme"


def overview_text(cat: Catalog, groups: list[Group], label: str) -> str:
    n = len(groups)
    if n == 1:
        p = groups[0].rep
        dept = cat.departments.get(p.department_id) if p.department_id else None
        desc = first_sentences(p.summary or p.overview, 330)
        bits = []
        yrs = _yrs(p.duration_years)
        camp = joined(campus_names(cat, groups[0].campus_ids))
        bits.append(f"**{p.name}** is a {yrs + ' ' if yrs else ''}{p.degree_type} programme" + (f" offered at {camp}" if camp else "") + (f" in the {dept.name} department" if dept else "") + ".")
        if p.specialization:
            bits.append(f"It focuses on {p.specialization}.")
        elif p.specialization_count:
            bits.append(f"It has {p.specialization_count} specialisations.")
        return (desc + "\n\n" if desc else "") + " ".join(bits)
    degrees = Counter(g.rep.degree_type for g in groups)
    campuses = list(dict.fromkeys(c for g in groups for c in campus_names(cat, g.campus_ids)))
    variants = variant_summary(groups) if n <= 12 else None
    durs = Counter(_yrs(g.rep.duration_years) for g in groups if _yrs(g.rep.duration_years))
    fields = [f for f, _ in facets(groups, "field")[:5]]
    head = f"CHRIST offers {n} {label} options" + (f", offered at {joined(campuses[:4])}" if campuses else "") + "."
    parts = [head]
    if len(degrees) > 1:
        parts.append("They span " + joined([f"{d} ({c})" for d, c in degrees.most_common(4)]) + ".")
    if variants:
        parts.append(variants)
    elif len(fields) > 1:
        parts.append("They cover areas such as " + joined(fields[:5]) + ".")
    if durs:
        d, c = durs.most_common(1)[0]
        parts.append(f"{'Most' if c < n else 'They all'} run for {d.replace('-year', ' years')}." if len(durs) == 1 or c * 2 >= n else "Durations vary by programme.")
    return " ".join(parts)


def availability_text(cat: Catalog, groups: list[Group], label: str) -> str:
    if not groups:
        return f"I can't find **{label}** in the CHRIST programme records, so I can't say it's offered."
    n = len(groups)
    names = [g.rep.name for g in groups[:4]]
    camp = joined(list(dict.fromkeys(c for g in groups[:4] for c in campus_names(cat, g.campus_ids))))
    if n == 1:
        return f"Yes, CHRIST offers **{names[0]}**" + (f" at {camp}." if camp else ".")
    return f"Yes. CHRIST has {n} matching options, for example {joined(names)}" + (f" ({n - 4} more)" if n > 4 else "") + (f", offered at {camp}." if camp else ".")


# ------------------------------------------------------------------------------------------------ eligibility and other sections across programmes
def _best_section(p: Program, kind: SectionKind):
    ss = sorted(p.sections.get(kind, ()), key=lambda s: (s.audience not in (Audience.ALL, Audience.INDIAN), -(int(s.academic_year[:4]) if s.academic_year else 0)))
    return ss[0] if ss else None


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def section_overview(groups: list[Group], kind: SectionKind, label: str, what: str, limit: int = 900, brief: bool = False) -> str | None:
    """The published text across several programmes: said once when identical, otherwise the common version plus a note that it varies."""
    got = [(g, s) for g in groups if (s := _best_section(g.rep, kind))]
    if not got:
        return None
    clusters: dict[str, list] = {}
    for g, s in got:
        clusters.setdefault(_norm(s.text)[:600], []).append((g, s))
    ranked = sorted(clusters.values(), key=len, reverse=True)
    top = ranked[0]
    text = first_sentences(top[0][1].text.strip(), limit) if len(top[0][1].text) > limit else top[0][1].text.strip()
    year = top[0][1].academic_year
    n, m = len(top), len(groups)
    if len(ranked) == 1 and n == m:
        lead = (f"Here's the published {what} for **{top[0][0].rep.name}**:" if m == 1 else f"The published {what} is the same for all {m} {label} options:")
        return f"{lead}\n\n{text}" + (f"\n\n_Published for {year}._" if year else "")
    others = [g.rep.name for c in ranked[1:] for g, _ in c][:3]
    missing = m - len(got)
    if brief:                                                   # inside a longer explanation: the common version and one line about the variation
        tail = f"\n\n_{what.capitalize()} is published per programme; this version applies to {n} of the {m} options" + (f" (others differ, for example {others[0]})" if others else "") + "._"
        return f"The most common published {what}:\n\n{text}{tail}"
    note = f"\n\nThe {what} is published separately for each programme, so it varies. This version applies to {n} of the {m} {label} options"
    note += f"; others differ, for example {joined(others)}." if others else "."
    if missing:
        note += f" For {missing} option{'s' if missing != 1 else ''} nothing is published in the database."
    return f"Here's the most common published {what} for {label}:\n\n{text}{note}\nTell me which programme you mean and I'll give you its exact {what}."


# ------------------------------------------------------------------------------------------------ comparison
def compare_table(cat: Catalog, per_degree: dict[str, list[Group]], topic: str) -> str:
    from .. import live  # noqa: F401  (kept for symmetry: no fee figures in a comparison)
    gl = {t.degree: t for t in glossary().values() if t.degree}
    degs = list(per_degree)
    head = "| | " + " | ".join(degs) + " |"
    rows = [head, "|---|" + "---|" * len(degs)]

    def row(label, f):
        rows.append(f"| {label} | " + " | ".join(str(f(d, per_degree[d])).replace("|", "/").replace("\n", " ") for d in degs) + " |")

    row("What it is (in general)", lambda d, g: (gl[d].focus.capitalize() if d in gl and gl[d].focus else "Not described"))
    row("Level", lambda d, g: (g[0].rep.academic_level if g else (gl[d].level or "").capitalize() if d in gl else "Not listed"))
    row("Programmes at CHRIST", lambda d, g: (f"{len(g)}" + (f" (e.g. {joined([x.rep.name for x in g[:2]])})" if g else "")) if g else "None found")
    row("Duration", lambda d, g: joined(sorted({_yrs(x.rep.duration_years).replace('-year', ' years') for x in g if _yrs(x.rep.duration_years)})) or "Not listed" if g else "Not listed")
    row("Campuses", lambda d, g: joined(list(dict.fromkeys(c for x in g for c in campus_names(cat, x.campus_ids)))[:3]) or "Not listed" if g else "Not listed")

    def elig(d, g):
        s = next((s for x in g if (s := _best_section(x.rep, SectionKind.ELIGIBILITY))), None)
        return first_sentences(s.text, 150) if s else "Not published"
    row("Eligibility (as published)", elig)
    return "\n".join(rows)


# ------------------------------------------------------------------------------------------------ concept explanations
def concept_text(terms: list[Term], chris: dict[str, str] | None = None) -> str:
    """The general meaning of one or two terms, then the CHRIST-specific sentences supplied by the caller (already retrieved from the records)."""
    def is_(t: Term) -> str:
        return f"{t.name} {t.summary}." if t.summary.startswith("means") else f"{t.name} is {t.summary}."
    if len(terms) == 1:
        base = is_(terms[0])
    else:
        a, b = terms[0], terms[1]
        base = f"{is_(a)} {b.name}, by contrast, is {b.summary}." if a.level != b.level else f"{is_(a)} {b.name} is {b.summary}."
        if a.level and b.level and a.level != b.level:
            base += f" So the practical difference is the level: {a.level} versus {b.level}."
    extra = " ".join(v for v in (chris or {}).values() if v)
    return base + (f"\n\n{extra}" if extra else "")
