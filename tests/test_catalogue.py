"""Programme resolution and caching over the database-backed catalogue."""
import pytest

from app.catalogue import get_catalogue, reset_catalogue, window_status
from app.sources import SourceUnavailable, set_source
from tests.conftest import CLOSE, FakeSource, OPEN


def test_resolve_picks_exact_programme_and_reports_ambiguity():
    c = get_catalogue()
    r = c.resolve("what are the mba fees")
    assert r.best.id == 334 and not r.ambiguous  # an exact name match wins over "MBA (Executive)"
    assert c.resolve("MBA Executive fees").best.id == 341
    assert c.resolve("master of business administration").best.id == 334
    assert c.resolve("business administration").ambiguous  # BBA / MBA / Executive MBA all fit: ask, don't guess
    bba = c.resolve("BBA")
    assert {p.id for p in bba.candidates} == {101, 102} and not bba.ambiguous  # one programme on two campuses
    assert c.resolve("tell me about b.com").best.id == 201
    assert c.resolve("phd in management").best.id == 501
    assert c.resolve("mca").best.id == 601


def test_question_words_never_select_a_programme():
    c = get_catalogue()
    for text in ("what are the application dates?", "last date to apply", "scholarship and placement information", "hello there", "quantum zebra spaceship"):
        assert c.resolve(text).best is None, text


def test_search_filters_and_stats():
    c = get_catalogue()
    assert {p.id for p in c.search("business", level="postgraduate")} == {334, 341}
    assert [p.id for p in c.search(campus="kengeri")] == [102]
    assert c.stats() == {"source": "CHRIST programme database", "programmes": 7, "undergraduate": 3, "postgraduate": 3, "doctoral": 1, "other": 0, "campuses": 2}
    assert c.by_id[501].level == "doctoral" and c.by_id[501].level_label == "Doctoral (Phd)"


def test_window_status_only_trusts_dates_that_say_something():
    assert window_status(OPEN, CLOSE) == "open"
    assert window_status("2017-12-08", None) == "unknown"          # an old opening date with no close date is not "open"
    assert window_status("2017-01-01", "2018-01-01") == "closed"
    assert window_status("2999-01-01", None) == "upcoming"
    assert window_status(None, None) == "unknown"


def test_catalogue_is_cached_per_source_and_rebuilt_when_it_changes():
    calls = []

    class Counting(FakeSource):
        def list_catalogue(self):
            calls.append(1)
            return super().list_catalogue()

    src = Counting()
    set_source(src)
    reset_catalogue()
    get_catalogue(), get_catalogue()
    assert len(calls) == 1
    set_source(Counting())
    get_catalogue()
    assert len(calls) == 2


def test_catalogue_raises_when_database_is_down_or_unset():
    set_source(FakeSource(down=True))
    reset_catalogue()
    with pytest.raises(SourceUnavailable):
        get_catalogue()
