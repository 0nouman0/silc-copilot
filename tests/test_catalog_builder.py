import pytest

from app.catalog import CatalogCheckFailed, SectionKind, build_catalog, check_catalog
from app.catalog.classify import detect_audience, detect_year, year_value
from app.catalog.clean import clean_html, parse_tables
from app.catalog.config import load_config
from app.catalog.models import Audience
from app.catalog.refresh import RefreshReport, refresh, sanity, set_catalog, set_reader, current_catalog
from app.sources.base import DataNotGranted
from tests.fake_catalog import FakeReader, ROWS, TODAY, build


def test_build_loads_core_programmes_with_levels_campuses_and_url():
    c = build()
    assert set(c.programs) == {334, 101, 501, 601}
    assert {p.level for p in c.programs.values()} == {"UG", "PG", "PhD"}
    mba = c.programs[334]
    assert mba.url == "https://christuniversity.in/courses/MzM0" and mba.erp_program_id == "9334" and mba.department_id == 10 and mba.school_id == 5
    assert c.programs[501].campus_id == 1 and c.programs[501].quality.campus_inferred          # campus_id 0 resolved through department_courses
    assert {pl.campus_id for pl in c.programs[101].placements} == {1, 24}                       # one program, two campuses
    assert c.programs[101].quality.no_erp_id and not mba.quality.no_erp_id
    assert c.campuses[1].admissions_email == "admissions@christuniversity.in" and "080 40129000" in c.campuses[1].phones
    assert any("no ERP program id" in w for w in c.build.warnings)


def test_no_fee_or_date_content_is_ever_loaded():
    c = build()
    titles = [s.title for p in c.programs.values() for ss in p.sections.values() for s in ss] + [s.title for s in c.shared_sections]
    assert not any("fee" in t.lower() or "dates" in t.lower() for t in titles)
    assert "5,70,000" not in " ".join(ch.text for ch in c.indexes.chunks)
    assert check_catalog(c) == []


def test_check_refuses_a_catalog_that_contains_a_fee_section():
    rows = {**ROWS, "sections": ROWS["sections"] + [{**ROWS["sections"][3], "rel_link_id": 99, "rel_link_name": "Fee Structure", "rel_link_category": "R"}]}
    # such a title is excluded at classification, so force it through a permissive config to prove the build check
    from app.catalog.config import CatalogConfig
    from dataclasses import replace
    import re
    cfg = replace(load_config(), exclude=(), rules=((re.compile("fee", re.I), SectionKind.MISC),) + load_config().rules)
    with pytest.raises(CatalogCheckFailed):
        build(FakeReader(rows), cfg)


def test_older_cycles_and_expiry_are_handled():
    p = build().programs[334]
    elig = p.sections[SectionKind.ELIGIBILITY]
    assert [s.academic_year for s in elig] == ["2027"] and elig[0].expires_on is not None and p.quality.expired_sections == 1   # 2025 dropped, 2027 flagged
    assert [s.academic_year for s in p.sections[SectionKind.ADMISSION_PROCESS]] == ["2027"]
    assert {d.filename for d in p.documents if d.kind == "syllabus"} == {"MBA-syllabus-2026.pdf"}                   # latest syllabus only
    names = {d.filename for d in p.documents}
    assert {"MBA-structure.pdf", "MBA-brochure-2027.pdf", "guide.pdf"} <= names
    assert all(d.url.startswith("https://christuniversity.in/uploads/userfiles/") for d in p.documents)


def test_overrides_can_mark_a_section_current_or_hide_it():
    from tests.fake_catalog import cfg_with
    c = build(cfg=cfg_with(current_ids=frozenset({1})))
    assert c.programs[334].quality.expired_sections == 0
    c = build(cfg=cfg_with(hide_ids=frozenset({1, 2})))
    assert SectionKind.ELIGIBILITY not in c.programs[334].sections


def test_sections_audience_shared_blocks_details_specs_and_curriculum():
    c = build()
    mba, bba = c.programs[334], c.programs[101]
    assert bba.sections[SectionKind.NRI][0].audience == Audience.NRI
    shared = [s for s in mba.sections[SectionKind.APPLICATION_PROCESS] if s.shared]
    assert len(shared) == 1 and shared[0].academic_year == "2027" and len(c.shared_sections) == 1      # dates block and non-core-only block skipped
    assert SectionKind.APPLICATION_PROCESS in bba.sections and SectionKind.APPLICATION_PROCESS not in c.programs[501].sections
    assert [s.text for s in mba.sections[SectionKind.CAREER_ROLES]] == ["Consultant"] and SectionKind.WHY_CHOOSE in mba.sections
    spec = mba.specializations[0]
    assert spec.name == "Finance" and spec.note == "Track note" and spec.roadmap[0].career_roles == ("Analyst", "Associate") and spec.roadmap[0].published_package.startswith("INR 6")
    assert mba.curriculum[0].subjects[0].name == "Managerial Economics" and mba.curriculum[0].subjects[0].credits == 3
    assert mba.highlights == ("Top recruiters", "Industry mentors")
    assert "AAAA" not in mba.overview and "finance" in mba.overview and SectionKind.OVERVIEW in mba.sections


def test_topics_facilities_and_departments():
    c = build()
    assert {i.title for i in c.topics["hostel"]} == {"Accommodation"} and c.topics["library"][0].campus_id == 24
    assert "facility" not in c.topics and len(c.topics.get("sports_clubs", ())) == 0            # 'tiny' text under 30 chars is skipped
    assert c.departments[10].placement_page == "Placement page" and c.indexes.by_topic[("hostel", 1)]


def test_indexes_alias_filter_and_bm25():
    c = build()
    ix = c.indexes
    assert ix.by_alias["mba27"] == (334,) and ix.by_alias["bba"] == (101,) and ix.by_alias["bachelorofbusinessadministrationbba"] == (101,)   # name, code, config alias
    assert ix.by_erp_id["9334"] == 334
    assert ix.by_filter("PG") == [334, 601] and ix.by_filter(campus_id=24) == [101, 601] and ix.by_filter("UG", campus_id=24) == [101]
    hit = ix.search("minimum marks bachelor degree eligible", 1)[0]
    assert hit.program_id == 334 and hit.kind == "eligibility" and hit.expired
    assert ix.search("hostel rooms outstation", 1, topic="hostel")[0].campus_id == 1
    shared = ix.search("upload your documents register", 3, program_id=101)
    assert any(h.program_ids and 101 in h.program_ids for h in shared) and not ix.search("register online", 3, program_id=501)
    assert ix.search("zzzz unknown") == []


def test_denied_optional_tables_degrade_to_warnings_but_required_ones_fail():
    c = build(FakeReader(denied={"settings", "shared_sections", "facilities"}))
    assert c.shared_sections == () and "hostel" not in c.topics and sum("no SELECT grant" in w for w in c.build.warnings) == 3
    assert c.campuses[1].admissions_email == "mail@christuniversity.in"                             # falls back to campus_master email
    with pytest.raises(DataNotGranted):
        build(FakeReader(denied={"programs"}))


def test_the_reader_only_touches_allow_listed_tables():
    r = FakeReader()
    build(r)
    from app.catalog.queries import ALLOWED_TABLES, QUERIES
    assert r.tables_read() <= ALLOWED_TABLES and len(ALLOWED_TABLES) == 16
    assert all(t in ALLOWED_TABLES for t, _ in QUERIES.values())
    forbidden = {"enquiry", "log", "member_payments", "pdp_registrations", "admin_rights", "department_rights", "subscription"}
    assert not forbidden & ALLOWED_TABLES and not any(f in sql for _, sql in QUERIES.values() for f in forbidden)
    from app.catalog.reader import MySqlReader
    with pytest.raises(KeyError):
        MySqlReader("h", 1, "d", "u", "p").rows("enquiry")


# ---------------------------------------------------------------- cleaning and classification
def test_clean_html_drops_images_unescapes_and_keeps_tables():
    assert "AAAA" not in clean_html('<p>a<img src="data:image/png;base64,AAAA"></p>')
    assert clean_html("&lt;p&gt;Escaped &amp;amp; fine&lt;/p&gt;") == "Escaped & fine"
    t = parse_tables(clean_html("<table><tr><th>Year</th><th>Seats</th></tr><tr><td>1</td><td>60</td></tr></table>"))
    assert t[0].header == ("Year", "Seats") and t[0].rows == (("1", "60"),)


def test_year_and_audience_detection():
    assert detect_year("Eligibility-2027") == "2027" and detect_year("Syllabus 2026-27") == "2026-27" and detect_year("Syllabus") is None
    assert year_value("2026-27") == 2027 and year_value("2026") == 2026 and year_value(None) is None
    assert detect_audience("NRI Student Category") == Audience.NRI and detect_audience("International category") == Audience.INTERNATIONAL and detect_audience("Eligibility") == Audience.ALL


# ---------------------------------------------------------------- refresh lifecycle
def test_refresh_swaps_skips_unchanged_and_reports_a_diff():
    r = FakeReader()
    rep = refresh(force=True, who="test", reader=r, extracted={})
    assert rep.swapped and len(rep.added) == 4 and current_catalog() is not None
    again = refresh(force=False, who="test", reader=r, extracted={})
    assert not again.swapped and "no source table changed" in again.reason
    r.bump()
    r.data["sections"][0]["rel_link_description"] = "<p>changed</p>"
    rep = refresh(force=False, who="test", reader=r, extracted={})
    assert rep.swapped and rep.updated == [334] and rep.as_dict()["triggered_by"] == "test"


def test_swap_is_refused_when_programmes_or_sections_collapse():
    r = FakeReader()
    refresh(force=True, reader=r, extracted={})
    old = current_catalog()
    r.data["programs"] = r.data["programs"][:1]
    rep = refresh(force=True, reader=r, extracted={})
    assert not rep.swapped and "dropped" in rep.reason and current_catalog() is old            # students keep the old catalog
    empty = build(FakeReader({**ROWS, "programs": []}))
    assert sanity(None, empty) == ["the new catalog has no programmes"]


def test_get_catalog_builds_on_first_use_and_unconfigured_is_unavailable():
    from app.catalog import get_catalog
    from app.sources.base import SourceUnavailable
    with pytest.raises(SourceUnavailable):
        get_catalog()
    set_reader(FakeReader())
    assert len(get_catalog().programs) == 4


def test_snapshot_is_written_as_jsonl(tmp_path):
    import json
    from app.catalog.snapshot import write_snapshot
    write_snapshot(build(), tmp_path / "s.jsonl")
    lines = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text(encoding="utf-8").split("\n") if x]
    assert lines[0]["_build"]["counts"]["programs"] == 4 and {x["id"] for x in lines[1:]} == {334, 101, 501, 601}
