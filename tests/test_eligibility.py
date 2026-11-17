from app.data.repository import get_repository
from app.eligibility import DISCLAIMER, ApplicantProfile, assess

repo = get_repository()
MBA = repo.get_eligibility("mba")
BSC = repo.get_eligibility("bsc-cs")
MSC = repo.get_eligibility("msc-ds")


def P(**k):
    return ApplicantProfile(**k)


def test_all_details_match():
    r = assess(BSC, P(highest_qualification="class12", stream="Science", subjects=["Mathematics", "Physics"],
                      percentage=72, completion_status="completed", has_backlogs=False))
    assert r.outcome == "appears_to_meet"
    assert r.outcome_label == "Appears to meet the published criteria"
    assert r.disclaimer == DISCLAIMER and "not an admission" in r.disclaimer


def test_one_detail_does_not_match():
    r = assess(BSC, P(highest_qualification="class12", stream="Science", subjects=["Mathematics"], percentage=40))
    assert r.outcome == "may_not_meet"
    assert any(c.criterion == "Minimum percentage" and c.status == "not_met" for c in r.checks)


def test_missing_information_unable_to_assess():
    r = assess(BSC, P(highest_qualification="class12"))
    assert r.outcome == "unable_to_assess"
    assert any(c.status == "missing_input" for c in r.checks)


def test_final_year_not_allowed():
    r = assess(MSC, P(highest_qualification="bachelor", subjects=["Mathematics"], percentage=80, completion_status="final_year"))
    assert r.outcome == "may_not_meet"


def test_final_year_allowed_but_backlog_unknown_needs_verification():
    r = assess(MBA, P(highest_qualification="bachelor", percentage=60, completion_status="final_year",
                      has_backlogs=True, entrance_taken=True, entrance_score=50))
    assert r.outcome == "official_verification_required"
    assert any(c.criterion == "Backlogs" and c.status == "needs_verification" for c in r.checks)


def test_backlog_not_allowed():
    r = assess(BSC, P(highest_qualification="class12", stream="Science", subjects=["Mathematics"], percentage=80, has_backlogs=True))
    assert r.outcome == "may_not_meet"


def test_foreign_qualification_requires_verification():
    r = assess(BSC, P(highest_qualification="class12", stream="Science", subjects=["Mathematics"], percentage=80,
                      qualification_country="Kenya"))
    assert r.outcome == "official_verification_required"


def test_entrance_required_not_taken():
    r = assess(MBA, P(highest_qualification="bachelor", percentage=70, entrance_taken=False))
    assert r.outcome == "may_not_meet"


def test_criteria_unavailable():
    r = assess(repo.get_eligibility("phd-mgmt"), P(highest_qualification="master", percentage=80))
    assert r.outcome == "unable_to_assess"
    assert r.checks[0].status == "criteria_unavailable"
