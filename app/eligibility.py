"""Deterministic preliminary eligibility assessment.

The LLM never decides eligibility: it calls this function and relays the result.
Output is always a PRELIMINARY indication — never an admission decision.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .data.models import EligibilityCriteria

Outcome = Literal["appears_to_meet", "may_not_meet", "unable_to_assess", "official_verification_required"]

OUTCOME_LABEL = {
    "appears_to_meet": "Appears to meet the published criteria",
    "may_not_meet": "May not meet one or more published criteria",
    "unable_to_assess": "Unable to assess — more information required",
    "official_verification_required": "Official verification required",
}
DISCLAIMER = ("This is a preliminary indication based on published criteria. It is not an admission "
              "decision, an official eligibility confirmation, or a guarantee of admission. "
              "The Admissions Office makes the official decision.")
_RANK = {"class12": 1, "bachelor": 2, "master": 3}


class ApplicantProfile(BaseModel):
    highest_qualification: Literal["class12", "bachelor", "master"] | None = None
    stream: str | None = None
    subjects: list[str] | None = None
    percentage: float | None = Field(default=None, ge=0, le=100)
    completion_status: Literal["completed", "final_year"] | None = None
    has_backlogs: bool | None = None
    entrance_taken: bool | None = None
    entrance_score: float | None = None
    qualification_country: str | None = None


class Check(BaseModel):
    criterion: str
    status: Literal["met", "not_met", "missing_input", "criteria_unavailable", "needs_verification"]
    detail: str


class EligibilityResult(BaseModel):
    outcome: Outcome
    outcome_label: str
    checks: list[Check]
    disclaimer: str = DISCLAIMER


def assess(criteria: EligibilityCriteria | None, p: ApplicantProfile) -> EligibilityResult:
    if criteria is None:
        c = [Check(criterion="Published criteria", status="criteria_unavailable",
                   detail="No published eligibility criteria are available for this programme.")]
        return _result(c)

    checks: list[Check] = []

    # Qualification level
    if criteria.min_qualification_level is None:
        checks.append(Check(criterion="Minimum qualification", status="criteria_unavailable",
                            detail="Minimum qualification is not published."))
    elif p.highest_qualification is None:
        checks.append(Check(criterion="Minimum qualification", status="missing_input",
                            detail=f"Required: {criteria.min_qualification_text}. Your highest qualification was not provided."))
    elif _RANK[p.highest_qualification] >= _RANK[criteria.min_qualification_level]:
        checks.append(Check(criterion="Minimum qualification", status="met",
                            detail=f"Required: {criteria.min_qualification_text}."))
    else:
        checks.append(Check(criterion="Minimum qualification", status="not_met",
                            detail=f"Required: {criteria.min_qualification_text}."))

    # Percentage
    if criteria.min_percentage is None:
        checks.append(Check(criterion="Minimum percentage", status="criteria_unavailable",
                            detail="Minimum percentage is not published."))
    elif p.percentage is None:
        checks.append(Check(criterion="Minimum percentage", status="missing_input",
                            detail=f"Required: {criteria.min_percentage:g}%. Your percentage was not provided."))
    else:
        ok = p.percentage >= criteria.min_percentage
        checks.append(Check(criterion="Minimum percentage", status="met" if ok else "not_met",
                            detail=f"Required {criteria.min_percentage:g}%; you reported {p.percentage:g}%."))

    # Subjects
    if criteria.required_subjects:
        if p.subjects is None:
            checks.append(Check(criterion="Required subjects", status="missing_input",
                                detail=f"Required: {', '.join(criteria.required_subjects)}. Subjects studied were not provided."))
        else:
            have = {s.strip().lower() for s in p.subjects}
            missing = [s for s in criteria.required_subjects if s.lower() not in have]
            checks.append(Check(criterion="Required subjects", status="not_met" if missing else "met",
                                detail=(f"Missing: {', '.join(missing)}." if missing
                                        else f"Required subjects present: {', '.join(criteria.required_subjects)}.")))

    # Stream
    if criteria.allowed_streams:
        if not p.stream:
            checks.append(Check(criterion="Stream", status="missing_input",
                                detail=f"Allowed: {', '.join(criteria.allowed_streams)}. Your stream was not provided."))
        else:
            ok = p.stream.strip().lower() in {s.lower() for s in criteria.allowed_streams}
            checks.append(Check(criterion="Stream", status="met" if ok else "not_met",
                                detail=f"Allowed: {', '.join(criteria.allowed_streams)}; you reported {p.stream}."))

    # Entrance exam
    ex = criteria.entrance_exam
    if ex is None:
        checks.append(Check(criterion="Entrance examination", status="criteria_unavailable",
                            detail="Entrance examination requirement is not published."))
    elif ex.required:
        label = ex.name or "Entrance examination"
        if p.entrance_taken is None:
            checks.append(Check(criterion=label, status="missing_input", detail="Required. Not stated whether you have taken it."))
        elif not p.entrance_taken:
            checks.append(Check(criterion=label, status="not_met", detail="Required, but you have not taken it yet."))
        elif ex.min_score is None:
            checks.append(Check(criterion=label, status="criteria_unavailable", detail="Minimum score is not published."))
        elif p.entrance_score is None:
            checks.append(Check(criterion=label, status="missing_input", detail=f"Minimum score {ex.min_score:g}. Your score was not provided."))
        else:
            ok = p.entrance_score >= ex.min_score
            checks.append(Check(criterion=label, status="met" if ok else "not_met",
                                detail=f"Minimum {ex.min_score:g}; you reported {p.entrance_score:g}."))

    # Final-year applicants
    if p.completion_status == "final_year":
        if criteria.final_year_allowed is None:
            checks.append(Check(criterion="Final-year applicants", status="needs_verification",
                                detail="Published criteria do not say whether final-year applicants may apply."))
        else:
            checks.append(Check(criterion="Final-year applicants",
                                status="met" if criteria.final_year_allowed else "not_met",
                                detail="Final-year applicants are allowed." if criteria.final_year_allowed
                                else "Final-year applicants are not allowed."))

    # Backlogs
    if p.has_backlogs:
        if criteria.backlogs_allowed is None:
            checks.append(Check(criterion="Backlogs", status="needs_verification",
                                detail="Published criteria do not say whether backlogs are acceptable."))
        else:
            checks.append(Check(criterion="Backlogs", status="met" if criteria.backlogs_allowed else "not_met",
                                detail="Backlogs are acceptable." if criteria.backlogs_allowed
                                else "Applicants with backlogs are not eligible."))

    # Foreign qualification
    if p.qualification_country and p.qualification_country.strip().lower() not in ("india", "in"):
        checks.append(Check(criterion="Foreign qualification", status="needs_verification",
                            detail="Foreign qualifications need official recognition/equivalence verification."
                            + (" An equivalence certificate is required." if criteria.foreign_needs_equivalence else "")))

    return _result(checks)


def _result(checks: list[Check]) -> EligibilityResult:
    st = {c.status for c in checks}
    if "not_met" in st:
        o: Outcome = "may_not_meet"
    elif st & {"missing_input", "criteria_unavailable"}:
        o = "unable_to_assess"
    elif "needs_verification" in st:
        o = "official_verification_required"
    else:
        o = "appears_to_meet"
    return EligibilityResult(outcome=o, outcome_label=OUTCOME_LABEL[o], checks=checks)
