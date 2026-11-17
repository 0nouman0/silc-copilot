"""Conservative preliminary eligibility hint.

CHRIST publishes eligibility as free text, not structured fields, so a full automatic
assessment would require guessing. This only compares the applicant's percentage with
percentages literally mentioned in the published criteria, and says so. It can flag
"may not meet" (below the LOWEST threshold mentioned) but can never confirm eligibility:
the best positive result is "official verification required".
"""
from __future__ import annotations

import re

LABEL = {
    "may_not_meet": "May not meet one or more published criteria",
    "unable_to_assess": "Unable to assess — more information required",
    "official_verification_required": "Official verification required",
}
DISCLAIMER = ("This is a preliminary indication based on the eligibility text published on the CHRIST website. It is not an admission "
              "decision, an official eligibility confirmation, or a guarantee of admission. The Admissions Office decides eligibility.")
_PCT = re.compile(r"(\d{2}(?:\.\d+)?)\s*(?:%|percent|per cent)", re.I)
_CTX = re.compile(r"mark|aggregate|percentage|minimum|score|grade|CGPA|pass", re.I)


def thresholds(text: str) -> list[float]:
    vals = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if _CTX.search(sentence):
            vals += [float(m.group(1)) for m in _PCT.finditer(sentence) if 30 <= float(m.group(1)) <= 100]
    return sorted(set(vals))


def hint(criteria_text: str | None, percentage: float | None) -> dict:
    if not criteria_text:
        return {"outcome": "unable_to_assess", "label": LABEL["unable_to_assess"], "detail": "No eligibility text is published for this programme.",
                "thresholds": [], "disclaimer": DISCLAIMER}
    th = thresholds(criteria_text)
    if percentage is None:
        return {"outcome": "unable_to_assess", "label": LABEL["unable_to_assess"], "thresholds": th, "disclaimer": DISCLAIMER,
                "detail": "Share your percentage/marks so I can compare it with the published text."}
    if th and percentage < min(th):
        return {"outcome": "may_not_meet", "label": LABEL["may_not_meet"], "thresholds": th, "disclaimer": DISCLAIMER,
                "detail": f"You reported {percentage:g}%, below the lowest percentage mentioned in the published criteria ({min(th):g}%)."}
    detail = (f"You reported {percentage:g}%. The published text mentions: {', '.join(f'{t:g}%' for t in th)}. " if th else
              "The published text does not state a percentage I can compare. ")
    return {"outcome": "official_verification_required", "label": LABEL["official_verification_required"], "thresholds": th,
            "disclaimer": DISCLAIMER,
            "detail": detail + "Other criteria (subjects, stream, entrance/selection process, category) must be checked against the full text."}
