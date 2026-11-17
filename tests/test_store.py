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
