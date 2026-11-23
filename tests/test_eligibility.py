import json
from dataclasses import replace
from datetime import date

from app.catalog.config import parse_rule
from app.catalog.models import Audience
from app.eligibility import Profile, check_eligibility, compare_programs, rank_programs
from app.eligibility.extract import draft_rules
from app.eligibility.normalize import is_foreign_board, normalize_board, normalize_qualification, to_percent
from tests.fake_catalog import build


def rule(**kw):
    d = {"program_id": 334, "audience": "indian", "academic_year": "2027", "min_qualification": "UG degree", "min_percent": 50, "reviewed_by": "Admissions", "reviewed_on": date(2026, 11, 30)}
    d.update(kw)
    return parse_rule(d)


def with_rules(pid, *rules):
    c = build()
    p = replace(c.programs[pid], eligibility_rules=tuple(rules), quality=replace(c.programs[pid].quality, unreviewed_eligibility=False))
    return c, p


def test_unreviewed_drafts_are_ignored_and_reviewed_rules_load():
    assert parse_rule({"program_id": 1, "reviewed_by": None, "reviewed_on": None}) is None
    assert parse_rule({"program_id": 1, "reviewed_by": "A"}) is None
    assert rule().min_percent == 50 and rule().audience == Audience.INDIAN


def test_no_reviewed_rule_means_no_verdict_only_published_criteria():
    c = build()
    v = check_eligibility(Profile(qualification="UG degree", percent=90), c.programs[334])
    assert v.outcome == "cannot_decide" and "not yet reviewed" in v.reasons[0]


def test_verdicts_ask_one_question_then_decide():
    _, p = with_rules(334, rule(required_subjects=["Mathematics"]))
    assert check_eligibility(Profile(), p, "indian").missing == "qualification"
    assert check_eligibility(Profile(qualification="bachelor"), p, "indian").missing == "marks"
    assert check_eligibility(Profile(qualification="bachelor", percent=60), p, "indian").missing == "subjects"
    ok = check_eligibility(Profile(qualification="bachelor", percent=60, subjects=["Maths", "English"]), p, "indian")
    assert ok.outcome == "eligible" and all(c.status == "met" for c in ok.conditions) and "admissions office" in ok.disclaimer
    low = check_eligibility(Profile(qualification="bachelor", percent=45, subjects=["Mathematics"]), p, "indian")
    assert low.outcome == "not_eligible" and "short by 5.0 points" in low.reasons[0]
    nosub = check_eligibility(Profile(qualification="bachelor", percent=70, subjects=["English"]), p, "indian")
    assert nosub.outcome == "not_eligible" and "missing Mathematics" in nosub.reasons[0]


def test_qualification_level_cgpa_and_relaxations():
    _, p = with_rules(334, rule(relaxations={"SC/ST": 5}))
    assert check_eligibility(Profile(qualification="12th", percent=95), p, "indian").outcome == "not_eligible"       # needs a degree
    assert check_eligibility(Profile(qualification="master", percent=60), p, "indian").outcome == "eligible"        # higher qualification is fine
    assert check_eligibility(Profile(qualification="bachelor", cgpa=5.0, cgpa_scale=10), p, "indian").outcome == "not_eligible"   # 47.5%
    assert check_eligibility(Profile(qualification="bachelor", cgpa=8.0, cgpa_scale=10), p, "indian").outcome == "eligible"        # 76%
    r = check_eligibility(Profile(qualification="bachelor", percent=46, category="sc/st"), p, "indian")
    assert r.outcome == "eligible" and "relaxation" in r.conditions[1].detail
    assert check_eligibility(Profile(qualification="bachelor", percent=46, category="General"), p, "indian").outcome == "not_eligible"   # relaxation never assumed


def test_audience_boards_foreign_qualifications_and_entrance_tests():
    _, p = with_rules(334, rule(), rule(audience="nri", min_percent=55, entrance_tests=["CAT"], accepted_boards=["CBSE", "ICSE"]))
    assert check_eligibility(Profile(qualification="bachelor", percent=60), p).missing == "nationality"
    nri = check_eligibility(Profile(qualification="bachelor", percent=60, board="CBSE"), p, "nri")
    assert nri.outcome == "eligible" and nri.conditions[-1].status == "pending"                                          # entrance scores are checked at selection
    assert check_eligibility(Profile(qualification="bachelor", percent=60, board="State board"), p, "nri").outcome == "not_eligible"
    assert check_eligibility(Profile(qualification="bachelor", percent=90, board="IB"), p, "nri").outcome == "cannot_decide"
    assert check_eligibility(Profile(qualification="bachelor", percent=90, nationality="international"), p).outcome == "cannot_decide"


def test_normalisation_helpers():
    assert normalize_qualification("Class 12") == "10+2" and normalize_qualification("post graduate") == "PG degree" and normalize_qualification("B.Com") == "UG degree" and normalize_qualification("") is None
    assert normalize_board("cbse") == "CBSE" and normalize_board("Karnataka State Board") == "STATE" and is_foreign_board("IGCSE") and not is_foreign_board("ICSE")
    assert to_percent(70, 9, 10) == 70 and to_percent(None, 8, 10) == 76 and to_percent(None, 3.0, 4) == 75 and to_percent(None, None, None) is None


def test_rank_programs_filters_by_eligibility_level_and_marks_unverified():
    c, mba = with_rules(334, rule())
    progs = dict(c.programs)
    progs[334] = mba
    c = replace(c, programs=progs)
    prof = Profile(qualification="bachelor", percent=40, nationality="indian")
    out = rank_programs(c, prof, "business administration")
    names = [r["name"] for r in out["recommendations"]]
    assert "Master of Business Administration (MBA)" not in names                       # ineligible: never suggested
    assert all(r["level"] != "UG" for r in out["recommendations"])                      # a graduate is offered PG/PhD, not UG
    ok = rank_programs(c, Profile(qualification="bachelor", percent=70, nationality="indian"), "business administration")
    mba_r = next(r for r in ok["recommendations"] if r["programme_id"] == 334)
    assert mba_r["eligibility"]["verified"] is True and mba_r["url"].endswith("/MzM0")
    phd = next(r for r in ok["recommendations"] if r["level"] == "PhD") if any(r["level"] == "PhD" for r in ok["recommendations"]) else None
    assert phd is None or phd["eligibility"]["verified"] is False
    assert rank_programs(c, Profile(), "")["found"] is False and rank_programs(c, Profile(level="UG"), "quantum zebra")["found"] is False
    assert "predict" in ok["note"].lower()


def test_compare_programs_has_no_fees_and_skips_unknown_ids():
    c = build()
    res = compare_programs(c, [334, 101, 424242])
    assert [r["programme_id"] for r in res["programmes"]] == [334, 101]
    assert "fee" not in json.dumps(res).lower() or "5,70,000" not in json.dumps(res)
    assert "Candidates with a Bachelor" in res["programmes"][0]["eligibility"]


def test_draft_extractor_produces_unreviewed_drafts():
    d = {(x["program_id"], x["audience"]): x for x in draft_rules(build())}
    mba = d[(334, "all")]
    assert mba["min_percent"] == 50 and mba["min_qualification"] == "UG degree" and mba["reviewed_by"] is None and mba["academic_year"] == "2027"
    assert d[(101, "all")]["min_percent"] == 45 and d[(101, "all")]["min_qualification"] == "10+2" and (101, "nri") in d
    assert parse_rule(mba) is None                                                       # a draft can never be used by the checker
