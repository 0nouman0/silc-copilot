import pytest

from app.christ.eligibility_hint import DISCLAIMER, hint, thresholds
from app.christ.store import Corpus, DataNotReady, get_corpus, tokenize


def ids(r):
    return [p.id for p in r.candidates]


# ---------------------------------------------------------------- resolver
def test_abbreviation_resolves_to_exact_programme_not_variants():
    r = get_corpus().resolve("what are the mba fees")
    assert r.best.id == "334" and ids(r) == ["334"] and not r.ambiguous


def test_variant_is_selected_when_named():
    assert get_corpus().resolve("MBA Executive fees").best.id == "341"


def test_full_phrase_matches_like_abbreviation():
    assert get_corpus().resolve("master of business administration").best.id == "334"


def test_same_programme_on_two_campuses_returns_both_candidates():
    r = get_corpus().resolve("BBA")
    assert sorted(ids(r)) == ["101", "102"] and {p.name for p in r.candidates} == {"Bachelor of Business Administration (BBA)"}


def test_dotted_abbreviations():
    assert get_corpus().resolve("tell me about b.com").best.id == "201"
    assert get_corpus().resolve("phd in management").best.id == "501"


@pytest.mark.parametrize("text", ["what are the hostel facilities", "how do I apply", "hello there", "weather today"])
def test_non_programme_text_resolves_to_nothing(text):
    assert get_corpus().resolve(text).best is None


# ---------------------------------------------------------------- search
def test_search_finds_relevant_page_with_url():
    hits = get_corpus().search("hostel registration", k=3)
    assert hits and hits[0].chunk.url.endswith("/hostel") and hits[0].source().url.endswith("/hostel")


def test_search_gibberish_returns_nothing():
    assert get_corpus().search("quantum zebra spaceship") == []


def test_global_search_excludes_programme_scoped_chunks_but_scoped_search_finds_them():
    c = get_corpus()
    assert all(h.chunk.scope == "global" for h in c.search("eligibility aggregate marks bachelor"))
    scoped = c.search("minimum marks aggregate", programme_id="334")
    assert scoped and scoped[0].chunk.programme_id == "334" and "Eligibility" in scoped[0].chunk.heading


def test_generic_programme_tab_is_indexed_once_globally():
    hits = get_corpus().search("selection entrance test group discussion interview", k=5)
    assert hits and hits[0].chunk.kind == "page" and "Selection Process" in hits[0].chunk.heading


def test_category_filter_and_external_marking():
    c = get_corpus()
    assert [h.chunk.category for h in c.search("scholarship merit freeship", categories={"scholarships"})] == ["scholarships"]
    ext = c.search("cultural fest campus life")
    assert ext and ext[0].source().external is True


def test_stats_and_listing_filters():
    c = get_corpus()
    assert c.stats()["programmes"] == 6 and c.stats()["scraped_at"].startswith("2026-11-17")
    assert {p.level for p in c.search_programmes(level="doctoral")} == {"doctoral"}
    assert {p.campus for p in c.search_programmes(campus="kengeri")} == {"Bangalore Kengeri Campus"}
    assert c.search_programmes("commerce")[0].id == "201"


def test_tokenizer_handles_dots_and_plurals():
    assert "bcom" in tokenize("B.Com.") and tokenize("facilities") == ["facility"]


def test_missing_data_dir_raises_clear_error(tmp_path):
    with pytest.raises(DataNotReady) as e:
        Corpus(tmp_path / "empty")
    assert "python -m scraper.run" in str(e.value)


# ---------------------------------------------------------------- eligibility hint
def test_thresholds_extracted_only_from_relevant_sentences():
    assert thresholds("Minimum 50% marks in aggregate. Fees are 90% refundable in some cases.") == [50.0]


def test_hint_never_confirms_eligibility():
    h = hint("Pass at the +2 level with 50% marks.", 85)
    assert h["outcome"] == "official_verification_required" and "decision" in h["disclaimer"] and h["disclaimer"] == DISCLAIMER


def test_hint_flags_below_lowest_threshold_only():
    assert hint("Minimum marks: 45% for general and 50% for others", 40)["outcome"] == "may_not_meet"
    assert hint("Minimum marks: 45% for general and 50% for others", 47)["outcome"] == "official_verification_required"


def test_hint_unable_without_data():
    assert hint(None, 80)["outcome"] == "unable_to_assess" and hint("some text", None)["outcome"] == "unable_to_assess"


# ---------------------------------------------------------------- partial data + hot reload (scraper checkpoints)
def test_programmes_only_checkpoint_is_usable(tmp_path, monkeypatch):
    import json
    from tests.conftest import PROGRAMMES
    d = tmp_path / "only-programmes"
    d.mkdir()
    (d / "programmes.json").write_text(json.dumps(PROGRAMMES), "utf-8")
    c = Corpus(d)
    assert c.pages == [] and c.resolve("mba fees").best.id == "334" and c.search("hostel") == []


def test_corpus_reloads_when_scraper_writes_new_data(tmp_path, monkeypatch):
    import json
    import time
    from app.christ import store
    from tests.conftest import PAGES, PROGRAMMES
    monkeypatch.setenv("CHRIST_DATA_DIR", str(tmp_path))
    store.reset_corpus()
    (tmp_path / "programmes.json").write_text(json.dumps(PROGRAMMES[:2]), "utf-8")
    assert len(get_corpus().programmes) == 2
    (tmp_path / "programmes.json").write_text(json.dumps(PROGRAMMES), "utf-8")
    (tmp_path / "pages.jsonl").write_text("\n".join(json.dumps(p) for p in PAGES), "utf-8")
    assert len(get_corpus().programmes) == 2  # not re-checked yet (throttled)
    monkeypatch.setattr(store, "_checked", time.monotonic() - 60)
    assert len(get_corpus().programmes) == 6 and get_corpus().search("hostel registration")


def test_corpus_keeps_serving_old_data_if_new_file_is_half_written(tmp_path, monkeypatch):
    import json
    import time
    from app.christ import store
    from tests.conftest import PROGRAMMES
    monkeypatch.setenv("CHRIST_DATA_DIR", str(tmp_path))
    store.reset_corpus()
    (tmp_path / "programmes.json").write_text(json.dumps(PROGRAMMES), "utf-8")
    assert len(get_corpus().programmes) == 6
    (tmp_path / "programmes.json").write_text("[{\"id\": ", "utf-8")  # truncated JSON
    monkeypatch.setattr(store, "_checked", time.monotonic() - 60)
    assert len(get_corpus().programmes) == 6


# ---------------------------------------------------------------- readable titles + popup cleanup (real-site quirks)
def test_generic_page_titles_are_derived_from_the_url():
    from app.christ.store import page_title
    assert page_title("CHRIST UNIVERSITY", "https://christuniversity.in/admission/application-queries") == "Application queries"
    assert page_title("CHRIST UNIVERSITY", "https://christuniversity.in/center/C/IS/contact-us") == "Contact us (IS)"
    assert page_title("CHRIST UNIVERSITY", "https://christuniversity.in/hostel/main-campus/hostel-and-dining") == "Hostel and dining"
    assert page_title("Central Instrumentation Facilty", "https://cif.christuniversity.in/contact.php") == "Central Instrumentation Facilty"  # real titles are kept


def test_popup_debris_is_removed_but_content_kept():
    from app.christ.store import clean_popup
    raw = "Admissions\nEligibility criteria\n01\nCandidates with 50 % aggregate are eligible.\n02\nFinal-year applicants may apply."
    assert clean_popup(raw) == "Candidates with 50 % aggregate are eligible.\nFinal-year applicants may apply."
    assert clean_popup(None) is None and clean_popup("Admissions\n01") is None


def test_url_filter_restricts_search():
    c = get_corpus()
    assert all("contact" in h.chunk.url for h in c.search("admissions office", url_rx="contact"))
    assert c.search("admissions office", url_rx="no-such-page") == []
