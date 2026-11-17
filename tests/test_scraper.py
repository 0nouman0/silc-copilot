"""Scraper unit tests (no network): parsing, extraction, URL scoping, and resolver regressions on real catalogue naming."""
import asyncio
import json
from types import SimpleNamespace

from app.christ.store import Corpus
from scraper import run as S
from scraper.extract import extract
from tests.conftest import _prog


# ------------------------------------------------------------------ catalogue parsing
def _rows(*rows):
    return "".join(
        '<div class="row"><div class="acc-data-item-name"><a href="https://christuniversity.in//courses/%s">%s</a></div>%s'
        '<div class="acc-data-item"><a class="apply-btn" href="https://espro.christuniversity.in/Application/">Apply</a></div>'
        '<div class="acc-data-item"><button onclick="openpopupAdm(%s, \'Eligibility\');">Eligibility</button></div></div>' % (code, name, cells, cid)
        for code, name, cells, cid in rows)


class FakeFetcher:
    def __init__(self, html_by_type):
        self.html = html_by_type

    async def post(self, url, data):
        return SimpleNamespace(status=200, text=json.dumps({"desktopHtml": self.html.get(data["type"], "")}))


def test_catalogue_finds_campus_and_dates_by_content_not_position():
    ug = _rows(("MzM0", "Master of Business Administration (MBA)", '<div class="acc-data-item">Bangalore Central Campus</div><div class="acc-data-item">05-Sep-2026</div><div class="acc-data-item">24-Jan-2027</div>', 334))
    phd = _rows(("OTQ1", "Doctor of Philosophy (Artificial Intelligence)",
                 '<div class="acc-data-item">Yes</div><div class="acc-data-item">Yes</div><div class="acc-data-item">Bangalore Central Campus</div>'
                 '<div class="acc-data-item">13-Oct-2026</div><div class="acc-data-item">29-Nov-2026</div>', 945))
    progs = asyncio.run(S.catalogue(FakeFetcher({"PGP": ug, "PHD": phd})))
    mba, doc = progs["334"], progs["945"]
    assert (mba["campus"], mba["application_open"], mba["application_close"], mba["level"]) == ("Bangalore Central Campus", "2026-09-05", "2027-01-24", "postgraduate")
    assert doc["campus"] == "Bangalore Central Campus" and doc["application_close"] == "2026-11-29" and doc["level"] == "doctoral"
    assert doc["url"].endswith("/courses/OTQ1") and doc["apply_url"].startswith("https://espro.")


def test_course_ids_roundtrip_base64():
    assert S.b64(919) == "OTE5" and S.unb64("OTE5") == "919" and S.unb64("OTQ1") == "945"


# ------------------------------------------------------------------ extraction
HTML = """<html><head><title>Hostel &amp; Dining</title><meta name="description" content="About hostels"></head><body>
<nav>MENU ITEM SHOULD VANISH</nav><script>var x=1;</script><style>.a{}</style>
<h1>Hostels</h1><p>CHRIST has <b>separate</b> hostels for <a href="/fees">men</a> and women.</p>
<ul><li>Wi-Fi</li><li>Laundry</li></ul>
<table><tr><th>Room</th><th>Fee</th></tr><tr><td>Double</td><td>60,000</td></tr></table>
<a href="/uploads/brochure.pdf">Brochure</a><a href="mailto:x@y.z">mail</a><a href="javascript:void(0)">js</a>
<iframe src="/view/guide.PDF?x=1"></iframe><footer>FOOTER JUNK</footer></body></html>"""


def test_extract_cleans_text_tables_lists_and_discovers_links():
    pg = extract(HTML, "https://christuniversity.in/hostel")
    assert pg.title == "Hostel & Dining" and pg.description == "About hostels"
    assert "separate hostels for men and women." in pg.text  # inline tags merged, not split across lines
    assert "- Wi-Fi" in pg.text and "| Room | Fee |" in pg.text and "| Double | 60,000 |" in pg.text
    assert "MENU ITEM" not in pg.text and "FOOTER JUNK" not in pg.text and "var x" not in pg.text
    assert "https://christuniversity.in/fees" in pg.links and not any(l.startswith(("mailto", "javascript")) for l in pg.links)
    assert set(pg.pdfs) == {"https://christuniversity.in/uploads/brochure.pdf", "https://christuniversity.in/view/guide.PDF?x=1"}


def test_popup_style_blocks_are_removed():
    pg = extract("<style>.cu-box{color:red}</style><div>1. Admission Registration Fee: INR 8000/-</div>", "https://x")
    assert pg.text == "1. Admission Registration Fee: INR 8000/-"


# ------------------------------------------------------------------ URL scope (what we are allowed to crawl)
def test_canon_scopes_to_christ_public_hosts():
    c = S.canon
    assert c("https://www.christuniversity.in//admission-home/") == "https://christuniversity.in/admission-home"
    assert c("http://library.christuniversity.in/x") == "http://library.christuniversity.in/x"
    assert c("https://christbangalore.blogspot.com/2020/post") .startswith("https://christbangalore.blogspot.com")
    for bad in ("https://espro.christuniversity.in/Application/", "https://courses.christuniversity.in/login/index.php", "https://booking.christuniversity.in",
                "http://192.168.100.100:8090", "https://evil.com/christuniversity.in", "https://notchristuniversity.in/", "mailto:a@b.c", "ftp://christuniversity.in/x"):
        assert c(bad) is None, bad


def test_crawl_routes_course_pages_away_and_respects_caps(monkeypatch):
    cr = S.Crawl(SimpleNamespace(), max_pages=3)
    cr.push("https://christuniversity.in/courses/OTE5", 0)
    cr.push("https://christuniversity.in/css/site.css", 0)
    cr.push("https://christuniversity.in/photo.JPG", 0)
    assert cr.course_codes == {"OTE5"} and cr.q.qsize() == 0
    for i in range(10):
        cr.push(f"https://christuniversity.in/page{i}", 0)
    assert cr.q.qsize() == 3  # max_pages cap


# ------------------------------------------------------------------ parsing helpers
def test_quick_facts_both_layouts():
    a = S.quick_facts("Quick Facts\nDegree\nMBA\nDuration\n2\nMode\nDay Scholar\nMedium\nEnglish\nCampus\nBangalore Central Campus\nApply Now")
    assert a["duration"] == "2 years" and a["mode"] == "Day Scholar" and a["medium"] == "English"
    b = S.quick_facts("Open from: 2026-10-15 13:00:00 | Open until: 2026-12-06 23:45:00\n8\nSemester\nSpecialisations\n4 Yrs\nDuration\nApplications")
    assert b["duration"] == "4 years" and b["semesters"] == 8 and b["open_until"] == "2026-12-06"
    assert "duration" not in S.quick_facts("0 Yrs\nDuration")  # the site's "0 Yrs" placeholder is not a duration


def test_tabs_get_titles_and_lose_the_repeated_programme_name():
    tabs = S.tidy_tabs("MBA", [{"id": "1", "kind": "common", "text": "MBA\nAdmission Queries\nFor any queries contact the office at any time."}])
    assert tabs[0]["title"] == "Admission Queries" and tabs[0]["text"].startswith("Admission Queries")


def test_boilerplate_lines_repeated_across_pages_are_dropped():
    pages = [{"kind": "page", "text": f"Home\nAbout us\nunique content {i} is here"} for i in range(60)]
    S.drop_boilerplate(pages)
    assert pages[0]["text"] == "unique content 0 is here"


def test_categorize():
    assert S.categorize("https://christuniversity.in/hostel-and-dining", "") == "hostel"
    assert S.categorize("https://christuniversity.in/x", "Scholarships for students") == "scholarships"


def test_checkpoint_output_is_valid_and_loadable(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "ROOT", tmp_path)
    progs = {"334": {"id": "334", "code": "MzM0", "name": "MBA", "level": "postgraduate", "campus": "Bangalore Central Campus", "application_open": None,
                     "application_close": "2026-12-06", "url": "https://christuniversity.in/courses/MzM0", "apply_url": None, "brochure_url": None,
                     "eligibility": "50% marks", "fees": "INR 8000", "overview": "Quick Facts\nDuration\n2\nApply Now", "tabs": [], "_tab_ids": {"common": [], "related": []}}}
    meta = S.save(progs, [], [], [], phase="programmes", complete=False, started=0, requests=5)
    assert meta["complete"] is False and meta["programmes_with_fees"] == 1
    c = Corpus(tmp_path)  # pages.jsonl is empty at this stage; the app must still load
    assert c.resolve("mba").best.id == "334" and c.stats()["complete"] is False
    assert "_tab_ids" not in json.loads((tmp_path / "programmes.json").read_text("utf-8"))[0]


# ------------------------------------------------------------------ resolver regressions on REAL catalogue naming
def _real_names(tmp_path):
    spec = [("902", "Master of Business Administration", "Bangalore Kengeri Campus"), ("334", "Master of Business Administration (MBA)", "Bangalore Central Campus"),
            ("803", "Master of Business Administration (MBA)", "Pune Lavasa Campus"), ("341", "Master of Business Administration (MBA) (Executive)", "Bangalore Central Campus"),
            ("563", "Bachelor of Business Administration (BBA) (Honours/Honours with Research)", "Bangalore Central Campus"),
            ("775", "Bachelor of Business Administration (BBA) (Honours/Honours with Research)", "Bangalore Yeshwanthpur Campus"),
            ("565", "Bachelor of Business Administration (BBA) Decision Science", "Bangalore Central Campus"),
            ("11", "Master of Arts English Studies and Communication", "Bangalore Central Campus"), ("12", "Master of Arts (Philosophy)", "Bangalore Central Campus"),
            ("13", "Master of Arts Applied Economics", "Bangalore Central Campus"), ("21", "Doctor of Philosophy (PhD) in Law", "Bangalore Central Campus"),
            ("22", "Bachelor of Law (BA LLB) (Honours)", "Bangalore Central Campus"), ("23", "Bachelor of Law (BBA LLB) (Honours)", "Bangalore Central Campus")]
    (tmp_path / "programmes.json").write_text(json.dumps([_prog(i, n, "postgraduate", c) for i, n, c in spec]), "utf-8")
    return Corpus(tmp_path)


def test_real_naming_mba_is_one_programme_across_naming_variants_and_campuses(tmp_path):
    r = _real_names(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path).resolve("MBA fees")
    assert not r.ambiguous and r.best.name.startswith("Master of Business Administration") and "Executive" not in r.best.name
    assert {p.id for p in r.candidates} == {"902", "334", "803"}


def test_real_naming_bba_never_resolves_to_master_and_lists_variants(tmp_path):
    (tmp_path / "b").mkdir()
    r = _real_names(tmp_path / "b").resolve("BBA")
    assert r.ambiguous and not any(p.name.startswith("Master") for p in r.candidates)
    assert [p.name.startswith("Bachelor of Business Administration") for p in r.candidates[:3]] == [True, True, True]  # pure BBA first, BBA LLB later


def test_real_naming_unmatched_word_is_penalised_not_ignored(tmp_path):
    (tmp_path / "c").mkdir()
    assert _real_names(tmp_path / "c").resolve("ma english").best.name == "Master of Arts English Studies and Communication"


def test_real_naming_single_generic_word_lists_options_instead_of_picking(tmp_path):
    (tmp_path / "d").mkdir()
    c = _real_names(tmp_path / "d")
    assert c.resolve("law").ambiguous and not c.resolve("BA LLB").ambiguous and c.resolve("BA LLB").best.id == "22"


def test_programme_list_search_requires_everything_typed(tmp_path):
    """Regression: searching 'MBA' used to match every 'Master of ...' because expansions were OR-ed (142 results on the real catalogue)."""
    (tmp_path / "e").mkdir()
    c = _real_names(tmp_path / "e")
    assert {p.id for p in c.search_programmes("MBA")} == {"902", "334", "803", "341"}
    assert {p.id for p in c.search_programmes("MBA", level="postgraduate")} == {"902", "334", "803", "341"}
    assert c.search_programmes("xyzzy") == [] and c.search_programmes("fees") == []
    assert [p.id for p in c.search_programmes("ma english")] == ["11"]
    assert len(c.search_programmes(None)) == 13 and len(c.search_programmes("law")) == 3


# ------------------------------------------------------------------ CLI: status + single-instance guard
def _patch_root(monkeypatch, tmp_path):
    d = tmp_path / "scrape-root"  # not tmp_path itself: the autouse corpus fixture already writes files there
    d.mkdir()
    monkeypatch.setattr(S, "ROOT", d)
    monkeypatch.setattr(S, "LOCK", d / ".scrape.lock")
    return d


def test_status_when_nothing_has_run(monkeypatch, tmp_path, capsys):
    tmp_path = _patch_root(monkeypatch, tmp_path)
    assert S.cli(["status"]) == 0
    out = capsys.readouterr().out
    assert "not running" in out and "nothing saved yet" in out


def test_second_scrape_is_refused_while_one_is_alive(monkeypatch, tmp_path, capsys):
    tmp_path = _patch_root(monkeypatch, tmp_path)
    (tmp_path / ".scrape.lock").write_text("4242")

    async def boom(*a, **k):
        raise AssertionError("a second crawl must not start")

    monkeypatch.setattr(S, "main", boom)
    assert S.cli([]) == 1
    assert "already running" in capsys.readouterr().out and "4242" in capsys.readouterr().out + "4242"


def test_stale_lock_does_not_block_and_is_cleaned_up(monkeypatch, tmp_path):
    import os
    import time
    tmp_path = _patch_root(monkeypatch, tmp_path)
    lock = tmp_path / ".scrape.lock"
    lock.write_text("1")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    ran = []

    async def fake_main(max_pages, limit):
        ran.append((max_pages, limit))

    monkeypatch.setattr(S, "main", fake_main)
    assert S.cli(["--max-pages", "50", "--limit-programmes", "3"]) == 0 and ran == [(50, 3)]


def test_status_reports_partial_checkpoint(monkeypatch, tmp_path, capsys):
    tmp_path = _patch_root(monkeypatch, tmp_path)
    (tmp_path / "meta.json").write_text(json.dumps({"complete": False, "phase": "crawl-1500", "programmes": 348, "programmes_with_eligibility": 323,
                                                    "programmes_with_fees": 348, "programmes_with_tabs": 0, "pages": 900, "scraped_at": "2026-11-17T10:00:00+00:00"}))
    S.cli(["status"])
    out = capsys.readouterr().out
    assert "partial" in out and "crawl-1500" in out and "programmes=348" in out and "pages=900" in out
