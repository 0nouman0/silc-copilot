"""Programme-name normalisation: degree type, academic level, field, specialization and entry mode, derived from the stored name.

The database has no degree-type column: "BSc", "B.Sc" and "Bachelor of Science (Computer Science, Data Science/Honours/...)" are all just
names. This module turns a name into structured fields WITHOUT changing the stored name, and never invents anything that the name does
not say. Filtering "only B.Tech" must use `degree_type`, not the words "Computer Science".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (regex on the cleaned name, degree_type, academic_level). First match wins; the order puts specific phrases before generic ones.
_DEGREES: list[tuple[str, str, str]] = [
    (r"^(bachelor of technology|b\.?\s?tech)\b", "B.Tech", "Undergraduate"),
    (r"^(bachelor of engineering|b\.e\b)", "B.E", "Undergraduate"),
    (r"^(bachelor of computer applications|bca)\b", "BCA", "Undergraduate"),
    (r"^(bachelor of business administration|bba)\b", "BBA", "Undergraduate"),
    (r"^(bachelor of commerce|b\.?\s?com)\b", "B.Com", "Undergraduate"),
    (r"^(bachelor of science|b\.?\s?sc)\b", "BSc", "Undergraduate"),
    (r"^(bachelor of arts|b\.?\s?a)\b(?!\w)", "BA", "Undergraduate"),
    (r"^(bachelor of laws?|ll\.?b)\b", "LLB", "Undergraduate"),
    (r"^(bachelor of design|b\.?\s?des)\b", "B.Des", "Undergraduate"),
    (r"^(bachelor of education|b\.?\s?ed)\b", "B.Ed", "Undergraduate"),
    (r"^integrated teacher education", "Integrated", "Undergraduate"),
    (r"^(master of technology|m\.?\s?tech)\b", "M.Tech", "Postgraduate"),
    (r"^(master of business administration|mba)\b", "MBA", "Postgraduate"),
    (r"^(master of computer applications|mca)\b", "MCA", "Postgraduate"),
    (r"^(master of commerce|m\.?\s?com)\b", "M.Com", "Postgraduate"),
    (r"^(master of science|m\.?\s?sc)\b", "MSc", "Postgraduate"),
    (r"^(master of arts|m\.?\s?a)\b(?!\w)", "MA", "Postgraduate"),
    (r"^(master of laws?|ll\.?m)\b", "LLM", "Postgraduate"),
    (r"^master of social work|^msw\b", "MSW", "Postgraduate"),
    (r"^(post[- ]?graduate diploma|pg diploma|diploma / pg diploma)", "PG Diploma", "Postgraduate"),
    (r"^(doctor of philosophy|ph\.?d)\b", "PhD", "Doctoral"),
    (r"^diploma\b", "Diploma", "Undergraduate"),
    (r"^certificate\b", "Certificate", "Undergraduate"),
]
_LATERAL = re.compile(r"\s*[-–]?\s*\(?\s*lateral entry\s*\)?\s*$", re.I)
_NOISE_PAREN = {"one year pg", "one year", "pg", "phd", "mba", "executive", "honours", "hons", "honours with research"}
_HONOURS = re.compile(r"\b(honou?rs?(\s+with\s+research)?|hons\.?)\b", re.I)
_ALIAS = [(r"\bai\s*(&|and|/)?\s*ml\b", "Artificial Intelligence and Machine Learning"), (r"\bai\b", "Artificial Intelligence"), (r"\bml\b", "Machine Learning"),
          (r"\bcse\b", "Computer Science and Engineering"), (r"\biot\b", "Internet of Things")]


@dataclass(frozen=True)
class Classified:
    clean_name: str
    degree_type: str
    academic_level: str
    field: str
    specialization: str | None
    subjects: tuple[str, ...]
    entry: str                        # "regular" | "lateral"
    honours: bool


def clean_name(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\xa0", " ")).strip(" -")


def _title_fix(s: str) -> str:
    s = re.sub(r"^\s*specialization\s*(in|:)?\s*", "", s, flags=re.I).strip(" -:")
    for rx, full in _ALIAS:
        if re.fullmatch(rx, s.strip(), re.I):
            return full
    return s


def classify_programme(name: str | None, level_hint: str | None = None) -> Classified:
    n = clean_name(name)
    entry = "lateral" if _LATERAL.search(n) else "regular"
    n = _LATERAL.sub("", n).strip(" -")
    degree, level = "Other", {"UG": "Undergraduate", "PG": "Postgraduate", "PhD": "Doctoral"}.get(level_hint or "", "Undergraduate")
    matched_end = 0
    for rx, d, lv in _DEGREES:
        m = re.search(rx, n, re.I)
        if m:
            degree, level, matched_end = d, lv, m.end()
            break
    rest = n[matched_end:]
    rest = re.sub(r"^\s*\((?:%s)\)\s*" % "|".join(re.escape(x) for x in ("PhD", "MBA", "BSc", "BA", "BCA", "BBA", "MSc", "MA")), "", rest.strip(), flags=re.I)
    rest = re.sub(r"^\s*(in|of)\s+", "", rest.strip(), flags=re.I)
    parens = re.findall(r"\(([^()]*)\)", rest)
    outside = re.sub(r"\([^()]*\)", " ", rest)
    outside = re.sub(r"\s+", " ", outside).strip(" -,")
    outside = re.sub(r"\s*-\s*CHRIST.*$", "", outside, flags=re.I).strip(" -,")
    honours = bool(_HONOURS.search(n))
    spec, subjects, field = None, (), outside
    useful = [p for p in parens if _HONOURS.sub("", p).strip(" /,-").lower() not in _NOISE_PAREN and p.strip().lower() not in _NOISE_PAREN
              and not re.search(r"deemed to be|credits? exchange|\bwith the\b", p, re.I)]
    if useful:
        inner = _HONOURS.sub("", useful[0])
        parts = tuple(_title_fix(x) for x in re.split(r"[,/]", inner) if _title_fix(x))
        if outside:                       # "Computer Science and Engineering (Artificial Intelligence and Machine Learning)"
            spec = _title_fix(inner.strip(" /,-"))
            subjects = parts
        else:                             # "BSc (Computer Science, Data Science/Honours/...)": the parenthesis IS the subject list
            field = parts[0] if parts else ""
            subjects = parts
            spec = ", ".join(parts[1:]) or None
    elif not outside and parens:
        field = _HONOURS.sub("", parens[0]).strip(" /,-")
    return Classified(n, degree, level, field.strip(), spec, subjects, entry, honours)


def norm_key(name: str | None) -> str:
    """Duplicate-detection key: case, spacing, punctuation and lateral-entry marker removed."""
    n = _LATERAL.sub("", clean_name(name)).lower()
    return re.sub(r"[^a-z0-9]+", "", n)
