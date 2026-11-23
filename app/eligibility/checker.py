"""Deterministic eligibility verdicts from admissions-reviewed rules. The model never decides eligibility; it only explains this result.

Outcomes: eligible | not_eligible | need_info (one question for the missing field) | cannot_decide (no reviewed rule, foreign
qualification, or an unusual case: the published criteria and the admissions contact are returned instead).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ..catalog.models import Audience, Catalog, EligibilityRule, Program, SectionKind
from .normalize import QUAL_RANK, is_foreign_board, norm_subject, normalize_board, normalize_qualification, to_percent

DISCLAIMER = "This is based on published criteria; the final admission decision is made by the admissions office."

QUESTIONS = {
    "qualification": "What is the highest qualification you have completed (Class 12 / 10+2, a bachelor's degree, or a master's degree)?",
    "marks": "What were your marks: a percentage, or a CGPA (and is it out of 10 or 4)?",
    "subjects": "Which subjects did you study in your last qualification?",
    "board": "Which board or university was your last qualification from (for example CBSE, ICSE, a state board)?",
    "nationality": "Are you an Indian resident, an NRI, or an international applicant?",
}


class Profile(BaseModel):
    """What the applicant has told us. The frontend holds it and resends it every turn; the server keeps no session."""
    qualification: str | None = Field(default=None, max_length=60)        # "10+2" | "UG degree" | "PG degree" (free text is normalised)
    percent: float | None = Field(default=None, ge=0, le=100)
    cgpa: float | None = Field(default=None, ge=0, le=10)
    cgpa_scale: Literal[4, 10] | None = None
    board: str | None = Field(default=None, max_length=60)
    subjects: list[str] = Field(default_factory=list, max_length=20)
    category: str | None = Field(default=None, max_length=40)            # for rule-defined relaxations only, never assumed
    nationality: Literal["indian", "nri", "international"] | None = None
    entrance_scores: dict[str, float] = Field(default_factory=dict)
    interests: str | None = Field(default=None, max_length=300)
    preferred_campus: str | None = Field(default=None, max_length=80)
    level: Literal["UG", "PG", "PhD", "Diploma", "Certificate"] | None = None


@dataclass
class Condition:
    name: str
    status: Literal["met", "failed", "pending"]
    detail: str


@dataclass
class Verdict:
    outcome: Literal["eligible", "not_eligible", "need_info", "cannot_decide"]
    program_id: int
    reasons: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    missing: str | None = None
    question: str | None = None
    rule: EligibilityRule | None = None
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "program_id": self.program_id, "reasons": self.reasons, "missing_field": self.missing, "question": self.question,
                "conditions_checked": [{"condition": c.name, "status": c.status, "detail": c.detail} for c in self.conditions],
                "rule_reviewed_by": self.rule.reviewed_by if self.rule else None, "rule_year": self.rule.academic_year if self.rule else None, "disclaimer": self.disclaimer}


def pick_rule(program: Program, audience: Audience | None) -> EligibilityRule | None:
    """Reviewed rule for this audience (an 'all' rule is the fallback), newest academic year first."""
    rules = sorted(program.eligibility_rules, key=lambda r: r.academic_year, reverse=True)
    for r in rules:
        if audience and r.audience == audience:
            return r
    for r in rules:
        if r.audience == Audience.ALL:
            return r
    if audience is None:
        return rules[0] if rules else None
    return None


def check_eligibility(profile: Profile, program: Program, audience: Audience | str | None = None) -> Verdict:
    aud = Audience(audience) if isinstance(audience, str) else audience
    aud = aud or (Audience(profile.nationality) if profile.nationality else None)
    if not program.eligibility_rules:
        return Verdict("cannot_decide", program.id, ["Admissions has not yet reviewed an eligibility rule for this programme, so no verdict can be given. "
                                                      "Read the published criteria and confirm with the admissions office."])
    specific = {r.audience for r in program.eligibility_rules} - {Audience.ALL}
    if aud is None and specific:
        return Verdict("need_info", program.id, ["The criteria differ by applicant type."], missing="nationality", question=QUESTIONS["nationality"])
    rule = pick_rule(program, aud)
    if rule is None:
        return Verdict("cannot_decide", program.id, ["No reviewed rule covers this applicant type."])
    # foreign qualifications are never judged here
    if (aud == Audience.INTERNATIONAL and not profile.board) or is_foreign_board(profile.board):
        return Verdict("cannot_decide", program.id, ["Equivalence of a foreign qualification is decided by the admissions office."], rule=rule)

    conds: list[Condition] = []
    qual = normalize_qualification(profile.qualification)
    if rule.min_qualification and qual is None:
        return Verdict("need_info", program.id, missing="qualification", question=QUESTIONS["qualification"], rule=rule)
    pct = to_percent(profile.percent, profile.cgpa, profile.cgpa_scale)
    if (rule.min_percent is not None or rule.min_cgpa is not None) and pct is None:
        return Verdict("need_info", program.id, missing="marks", question=QUESTIONS["marks"], rule=rule)
    if rule.required_subjects and not profile.subjects:
        return Verdict("need_info", program.id, missing="subjects", question=QUESTIONS["subjects"], rule=rule)
    if rule.accepted_boards and not profile.board:
        return Verdict("need_info", program.id, missing="board", question=QUESTIONS["board"], rule=rule)

    if rule.min_qualification:
        need, have = QUAL_RANK.get(rule.min_qualification), QUAL_RANK.get(qual or "")
        if need and have:
            ok = have >= need
            conds.append(Condition("qualification", "met" if ok else "failed", f"requires {rule.min_qualification}; you have {qual}"))
    if rule.min_percent is not None and pct is not None:
        relax = 0.0
        if profile.category:
            relax = max((v for k, v in rule.relaxations.items() if k.lower() == profile.category.lower()), default=0.0)
        need = rule.min_percent - relax
        ok = pct >= need
        detail = f"requires {need:g}%" + (f" ({relax:g} point relaxation for {profile.category})" if relax else "") + f"; you have {pct:g}%"
        conds.append(Condition("marks", "met" if ok else "failed", detail + ("" if ok else f" (short by {need - pct:.1f} points)")))
    if rule.min_cgpa is not None and (profile.cgpa is not None):
        scale_ok = (profile.cgpa_scale or 10)
        ok = profile.cgpa >= rule.min_cgpa
        conds.append(Condition("cgpa", "met" if ok else "failed", f"requires CGPA {rule.min_cgpa:g}; you have {profile.cgpa:g} (scale {scale_ok})"))
    if rule.required_subjects:
        have_s = {norm_subject(s) for s in profile.subjects}
        lacking = [s for s in rule.required_subjects if norm_subject(s) not in have_s]
        conds.append(Condition("subjects", "failed" if lacking else "met", f"requires {', '.join(rule.required_subjects)}" + (f"; missing {', '.join(lacking)}" if lacking else "")))
    if rule.accepted_boards:
        nb = normalize_board(profile.board)
        ok = nb in {normalize_board(b) for b in rule.accepted_boards}
        conds.append(Condition("board", "met" if ok else "failed", f"accepted boards: {', '.join(rule.accepted_boards)}; yours: {profile.board}"))
    if rule.entrance_tests:
        taken = {k.upper() for k in profile.entrance_scores}
        got = [t for t in rule.entrance_tests if t.upper() in taken]
        conds.append(Condition("entrance test", "met" if got else "pending", f"accepted: {', '.join(rule.entrance_tests)}" + ("" if got else "; required scores are checked at selection")))
    failed = [c for c in conds if c.status == "failed"]
    if failed:
        return Verdict("not_eligible", program.id, [f"{c.name}: {c.detail}" for c in failed], conds, rule=rule)
    return Verdict("eligible", program.id, [f"You meet the published {rule.academic_year or ''} criteria that were checked.".replace("  ", " ")], conds, rule=rule)


def explain_only(catalog: Catalog, program: Program) -> dict:
    """What to show when no verdict is possible: the published criteria text, never a judgement."""
    ss = program.sections.get(SectionKind.ELIGIBILITY, ())
    return {"published_criteria": [{"title": s.title, "text": s.text[:3000], "academic_year": s.academic_year, "audience": s.audience.value,
                                   "expires_on": s.expires_on.isoformat() if s.expires_on else None} for s in ss[:3]]}
