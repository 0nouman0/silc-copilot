"""Scrape CHRIST (Deemed to be University) public information into data/christ/.

    python -m scraper.run            # full run (resumable; responses are cached)
    python -m scraper.run --max-pages 300   # quick smoke run

Outputs (data/christ/):
    programmes.json   structured catalogue: dates, eligibility, fees, tabs, brochure, ...
    pages.jsonl       every other page / PDF / external source as cleaned text
    meta.json         counts, timing, sources, scrape date
Only public pages are fetched. Nothing behind a login (ERP, Moodle, application portal) is touched.
"""
from __future__ import annotations

import argparse
import logging
import asyncio
import base64
import collections
import hashlib
import io
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .extract import extract
from .http import Fetcher

for _n in ("pypdf", "fontTools"):
    logging.getLogger(_n).setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent.parent / "data" / "christ"
LOCK = ROOT / ".scrape.lock"
STALE_AFTER_S = 600  # no heartbeat for 10 minutes => the previous run is dead
BASE = "https://christuniversity.in"
EXT_HOSTS = {"christbangalore.blogspot.com": "CHRIST blog (Blogspot)", "christuba.wordpress.com": "CHRIST blog (WordPress)"}
EXT_CAP = 40
SKIP_HOSTS = ("espro.", "courses.", "booking.", "mail.", "webmail.", "erp.")  # login-gated / transactional
HOST_CAP = {"main": 4000, "online.christuniversity.in": 400}
DEFAULT_SUB_CAP = 250
SKIP_PATH = re.compile(r"^/(php|css|js|images|img|fonts|assets)/|\.(jpe?g|png|gif|svg|webp|ico|css|js|zip|rar|mp4|mp3|avi|mov|docx?|xlsx?|pptx?)$", re.I)
LEVELS = {"UGP": "undergraduate", "PGP": "postgraduate", "PHD": "doctoral"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)
    try:  # heartbeat: lets `status` and the single-instance guard know a run is alive
        LOCK.touch()
    except OSError:
        pass


def canon(url: str) -> str | None:
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower().removeprefix("www.")
    if host in EXT_HOSTS:
        return urlunsplit(("https", host, re.sub(r"/+", "/", p.path).rstrip("/") or "/", "", ""))
    if not (host == "christuniversity.in" or host.endswith(".christuniversity.in")) or host.startswith(SKIP_HOSTS):
        return None
    path = re.sub(r"/+", "/", p.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    scheme = "https" if host == "christuniversity.in" else p.scheme
    return urlunsplit((scheme, host, path, p.query, ""))


def b64(n: int | str) -> str:
    return base64.b64encode(str(n).encode()).decode().rstrip("=")


def unb64(code: str) -> str | None:
    try:
        return base64.b64decode(code + "=" * (-len(code) % 4)).decode()
    except Exception:  # noqa: BLE001
        return None


def iso(d: str) -> str | None:
    try:
        return datetime.strptime(d.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


# ------------------------------------------------------------------ phase 1: programme catalogue
async def catalogue(f: Fetcher) -> dict[str, dict]:
    progs: dict[str, dict] = {}
    for typ, level in LEVELS.items():
        r = await f.post(f"{BASE}/php/ajax/ajax_course.php", {"type": typ, "campus": 1, "divi": 0, "dept": 0, "search": ""})
        if not r:
            log("catalogue failed for", typ)
            continue
        html = json.loads(r.text).get("desktopHtml", "")
        soup = BeautifulSoup(html, "lxml")
        for nd in soup.select("div.acc-data-item-name"):
            a = nd.find("a", href=True)
            if not a:
                continue
            row = nd.parent
            items = [re.sub(r"\s+", " ", d.get_text(" ")).strip() for d in row.select("div.acc-data-item")]
            # column layout differs by level (PhD rows have extra leading cells), so find values by content, not position
            campus = next((x for x in items if "campus" in x.lower()), None)
            dates = [iso(x) for x in items if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4}", x)]
            m = re.search(r"openpopupAdm\((\d+)", str(row))
            code = a["href"].rstrip("/").rsplit("/", 1)[-1]
            cid = m.group(1) if m else unb64(code)
            if not cid:
                continue
            brochure = next((x["href"] for x in row.find_all("a", href=True) if x["href"].lower().endswith(".pdf") or "brochure" in x.get_text().lower()), None)
            apply = next((x["href"] for x in row.select("a.apply-btn")), None)
            progs.setdefault(cid, {
                "id": cid, "code": b64(cid), "name": re.sub(r"\s+", " ", a.get_text(" ")).strip(), "level": level,
                "campus": campus, "application_open": dates[0] if dates else None,
                "application_close": dates[1] if len(dates) > 1 else None,
                "url": f"{BASE}/courses/{b64(cid)}", "apply_url": apply, "brochure_url": brochure})
        log(f"catalogue {typ}: total so far {len(progs)}")
    return progs


# ------------------------------------------------------------------ phase 2: general crawl
class Crawl:
    def __init__(self, f: Fetcher, max_pages: int, on_progress=None):
        self.f, self.max_pages, self.on_progress = f, max_pages, on_progress
        self.q: asyncio.Queue = asyncio.Queue()
        self.seen: set[str] = set()
        self.pages: list[dict] = []
        self.pdfs: dict[str, str] = {}  # url -> referring page
        self.course_codes: set[str] = set()
        self.per_host: collections.Counter = collections.Counter()

    def push(self, url: str, depth: int):
        u = canon(url)
        if not u or u in self.seen:
            return
        p = urlsplit(u)
        if SKIP_PATH.search(p.path):
            return
        m = re.match(r"^/+courses/([A-Za-z0-9=]+)$", p.path)
        if m and p.netloc == "christuniversity.in":
            self.course_codes.add(m.group(1).rstrip("="))
            return
        cap = EXT_CAP if p.netloc in EXT_HOSTS else HOST_CAP.get(p.netloc, HOST_CAP["main"] if p.netloc == "christuniversity.in" else DEFAULT_SUB_CAP)
        if self.per_host[p.netloc] >= cap or len(self.seen) >= self.max_pages:
            return
        self.seen.add(u)
        self.per_host[p.netloc] += 1
        self.q.put_nowait((u, depth))

    async def worker(self):
        while True:
            u, depth = await self.q.get()
            try:
                r = await self.f.get(u)
                if r and r.status == 200 and canon(r.final_url):  # a redirect off-scope (e.g. a Google sign-in wall) is discarded
                    ct = r.content_type.lower()
                    if "pdf" in ct:
                        self.pdfs.setdefault(r.final_url, u)
                    elif "html" in ct:
                        pg = extract(r.text, r.final_url)
                        for l in pg.links:
                            self.push(l, depth + 1)
                        for pdf in pg.pdfs:
                            if canon(pdf):
                                self.pdfs.setdefault(pdf, u)
                        if pg.text:
                            host = urlsplit(u).netloc
                            rec = {"url": u, "host": host, "title": pg.title, "description": pg.description,
                                   "text": pg.text, "kind": "external" if host in EXT_HOSTS else "page", "depth": depth}
                            if host in EXT_HOSTS:
                                rec["source_label"] = EXT_HOSTS[host]
                            self.pages.append(rec)
                if self.on_progress and len(self.pages) % 500 == 0 and self.pages:
                    self.on_progress(len(self.pages))
                if len(self.pages) % 100 == 0 and self.pages:
                    log(f"crawl: {len(self.pages)} pages, queue {self.q.qsize()}, pdfs {len(self.pdfs)}, courses {len(self.course_codes)}, req {self.f.requests}")
            except Exception as e:  # noqa: BLE001
                log("crawl error", u, repr(e)[:120])
            finally:
                self.q.task_done()

    async def run(self, seeds: list[str], workers: int = 6):
        for s in seeds:
            self.push(s, 0)
        ws = [asyncio.create_task(self.worker()) for _ in range(workers)]
        await self.q.join()
        for w in ws:
            w.cancel()


# ------------------------------------------------------------------ phase 3: programme detail pages + tabs
async def course_core(f: Fetcher, p: dict) -> dict:
    """Fast, high-value part: programme page (overview/quick facts), Eligibility and Fees popups."""
    cid = p["id"]

    async def popup(kind: str):
        r = await f.post(f"{BASE}/php/ajax/ajax_admissionPopup.php", {"course_id": cid, "type": kind})
        return extract(r.text, BASE).text if r and r.status == 200 and r.text.strip() else None

    page, elig, fees = await asyncio.gather(f.get(p["url"]), popup("Eligibility"), popup("Fees"))
    p["eligibility"], p["fees"] = elig, fees
    if page and page.status == 200:
        pg = extract(page.text, page.final_url)
        p["title"], p["overview"] = pg.title, pg.text
        p["pdfs"] = list(pg.pdfs)
        if not p.get("name") or p["name"].startswith("Programme "):
            h = re.search(r"<h1[^>]*>(.*?)</h1>", page.text, re.S)
            if h:
                p["name"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h.group(1))).strip() or p["name"]
        p["_tab_ids"] = {"common": list(dict.fromkeys(re.findall(r"getcontnt\((\d+)\)", page.text))),
                         "related": list(dict.fromkeys(re.findall(r"getrcontnt\((\d+)\)", page.text)))}
    else:
        p.setdefault("overview", None)
        p["_tab_ids"] = {"common": [], "related": []}
    p.setdefault("tabs", [])
    return p


async def course_tabs(f: Fetcher, p: dict) -> dict:
    """Slow part: the ~25 lazy-loaded sections per programme page."""
    cid, ids = p["id"], p.get("_tab_ids") or {"common": [], "related": []}

    async def tab(kind: str, lid: str):
        ep = "get_common_content" if kind == "common" else "get_related_content"
        data = {"link_id": lid, **({"courseid": cid} if kind == "common" else {})}
        r = await f.post(f"{BASE}/php/ajax/{ep}.php", data)
        if not r or r.status != 200 or not r.text.strip():
            return None
        t = extract(r.text, BASE).text
        return {"id": lid, "kind": kind, "text": t} if t else None

    tabs = await asyncio.gather(*[tab("common", i) for i in ids["common"]], *[tab("related", i) for i in ids["related"]])
    p["tabs"] = [t for t in tabs if t]
    p["_tabs_done"] = True
    return p


def quick_facts(text: str) -> dict:
    """Pull duration / semesters / mode / medium / dates from either page layout."""
    t, out = text or "", {}
    m = re.search(r"Quick Facts\s*\n(.*?)(?:\nApply Now|\nProgramme Overview|\Z)", t, re.S)
    if m:
        lines = [x.strip() for x in m.group(1).split("\n") if x.strip()]
        keys = {"Degree": "degree", "Duration": "duration", "Mode": "mode", "Medium": "medium", "Campus": "campus", "Open Until": "open_until"}
        for i, ln in enumerate(lines[:-1]):
            if ln in keys and lines[i + 1] not in keys:
                out[keys[ln]] = lines[i + 1]
    if (d := re.search(r"([\d.]+)\s*Yrs\s*\n\s*Duration", t)) and float(d.group(1)) > 0:
        out["duration"] = f"{d.group(1)} years"
    elif out.get("duration", "").replace(".", "").isdigit() and float(out["duration"]) > 0:
        out["duration"] = f"{out['duration']} years"
    else:
        out.pop("duration", None)
    if (sm := re.search(r"(\d+)\s*\n\s*Semester\b", t)) and int(sm.group(1)) > 0:
        out["semesters"] = int(sm.group(1))
    if (o := re.search(r"Open from:\s*(\S+)(?: \S+)?\s*\|\s*Open until:\s*(\S+)", t)):
        out["open_from"], out["open_until"] = o.group(1), o.group(2)
    return out


def tidy_tabs(name: str, tabs: list[dict]) -> list[dict]:
    """Tab text = [programme name, tab title, content...]. Derive a title, drop the repeated name."""
    out = []
    for tb in tabs:
        lines = tb["text"].split("\n")
        if lines and (lines[0].strip() == name or name.startswith(lines[0].strip()[:30])):
            lines = lines[1:]
        title = re.sub(r"^[#|\s-]+|[|\s]+$", "", lines[0])[:90] if lines else ""
        out.append({"id": tb["id"], "kind": tb["kind"], "title": title or f"Section {tb['id']}", "text": "\n".join(lines).strip()})
    return [t for t in out if len(t["text"]) > 20]


# ------------------------------------------------------------------ pdfs
def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        rd = PdfReader(io.BytesIO(data))
        if rd.is_encrypted:
            return ""
        parts = []
        for pg in rd.pages[:250]:
            t = (pg.extract_text() or "").strip()
            if t:
                parts.append(re.sub(r"[ \t]+", " ", t))
        return "\n".join(parts)[:400_000]
    except Exception:  # noqa: BLE001
        return ""


async def fetch_pdfs(f: Fetcher, urls: dict[str, str], cap: int) -> list[dict]:
    out, sem = [], asyncio.Semaphore(3)

    async def one(u: str, ref: str):
        async with sem:
            r = await f.get(u, max_bytes=25_000_000)
        if not r or r.status != 200 or not canon(r.final_url) or b"%PDF" not in r.body[:1024]:
            return
        txt = await asyncio.to_thread(pdf_text, r.body)
        if len(txt) > 200:
            out.append({"url": u, "host": urlsplit(u).netloc, "title": Path(urlsplit(u).path).name, "description": f"PDF linked from {ref}",
                        "text": txt, "kind": "pdf", "depth": 9})

    await asyncio.gather(*[one(u, ref) for u, ref in list(urls.items())[:cap]])
    return out


# ------------------------------------------------------------------ external (open) sources
# Wikipedia (requires contact details in the User-Agent) and NIRF (blocks bots, 403) are intentionally not scraped.
EXTERNAL: list = []


async def external(f: Fetcher) -> list[dict]:
    out = []
    for api, title, url, src in EXTERNAL:
        r = await f.get(api)
        if not r or r.status != 200:
            log("external failed:", title)
            continue
        try:
            pages = json.loads(r.text)["query"]["pages"]
            text = next(iter(pages.values())).get("extract", "")
        except (KeyError, ValueError, StopIteration):
            continue
        if text:
            text = re.sub(r"\n={2,}\s*(.*?)\s*={2,}\n", lambda m: f"\n## {m.group(1)}\n", text)
            out.append({"url": url, "host": urlsplit(url).netloc, "title": title, "description": f"External source: {src}",
                        "text": text, "kind": "external", "source_label": src, "depth": 0})
    return out


# ------------------------------------------------------------------ finalize
CATEGORIES = [
    ("fees", r"fee|tuition|payment"), ("scholarships", r"scholar|financial-aid|financial aid|concession|freeship"),
    ("admissions", r"admission|apply|application|eligib|selection|entrance|cuet|chrt"), ("hostel", r"hostel|accommodation|dining|mess"),
    ("placements", r"placement|recruit|career-?(guidance|development)|internship"), ("international", r"international|foreign|nri|visa"),
    ("campus", r"campus|facilit|infrastructure|sports|transport|library|medical|health|cafeteria|gym"),
    ("programmes", r"programme|course|curriculum|syllabus|academics|department|school-of|faculty-of"),
    ("research", r"research|phd|doctoral|publication"), ("examination", r"exam|result|evaluation|revaluation"),
    ("contact", r"contact|reach-us|enquir|helpline"), ("about", r"about|history|vision|mission|accredit|ranking|naac|nirf|leadership"),
    ("events", r"event|news|blog|media|announcement|gallery"),
]


def categorize(url: str, title: str, text: str = "") -> str:
    hay = (urlsplit(url).path + " " + title + " " + text[:300]).lower()
    for cat, rx in CATEGORIES:
        if re.search(rx, hay):
            return cat
    return "general"


def drop_boilerplate(pages: list[dict], threshold: float = 0.12) -> None:
    html_pages = [p for p in pages if p["kind"] == "page"]
    if len(html_pages) < 40:
        return
    freq = collections.Counter(ln for p in html_pages for ln in set(p["text"].split("\n")))
    limit = max(15, int(len(html_pages) * threshold))
    common = {ln for ln, c in freq.items() if c >= limit}
    for p in html_pages:
        p["text"] = "\n".join(ln for ln in p["text"].split("\n") if ln not in common)
    log(f"boilerplate: removed {len(common)} repeated lines (appearing on >= {limit} pages)")


def build_outputs(progs: dict, crawl_pages: list, pdf_pages: list, ext: list):
    """Pure function: raw collected data -> final programmes list + cleaned pages (works on partial data too)."""
    import copy
    pages = copy.deepcopy(crawl_pages) + copy.deepcopy(pdf_pages) + copy.deepcopy(ext)
    drop_boilerplate(pages)
    seen_hash, final = set(), []
    for p in pages:
        h = hashlib.sha1(p["text"].encode()).hexdigest()
        if len(p["text"]) < 60 or h in seen_hash:
            continue
        seen_hash.add(h)
        p["category"] = categorize(p["url"], p["title"], p["text"] if p["kind"] == "pdf" else "")
        p["fetched_at"] = date.today().isoformat()
        final.append(p)
    plist = []
    for p in progs.values():
        q = {k: v for k, v in p.items() if not k.startswith("_") and k != "pdfs"}
        q["quick_facts"] = quick_facts(p.get("overview") or "")
        q["tabs"] = tidy_tabs(p["name"], p.get("tabs", []))
        q["category"] = "programmes"
        plist.append(q)
    plist.sort(key=lambda x: (x["level"], x["name"]))
    return plist, final


def save(progs, crawl_pages, pdf_pages, ext, *, phase: str, complete: bool, started: float, requests: int):
    plist, final = build_outputs(progs, crawl_pages, pdf_pages, ext)
    ROOT.mkdir(parents=True, exist_ok=True)

    def atomic(path: Path, text: str):  # the app may read while we write
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(path)

    atomic(ROOT / "programmes.json", json.dumps(plist, ensure_ascii=False, indent=1))
    atomic(ROOT / "pages.jsonl", "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in final))
    meta = {"scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_site": BASE, "complete": complete, "phase": phase,
            "programmes": len(plist), "programmes_with_eligibility": sum(1 for p in plist if p.get("eligibility")),
            "programmes_with_fees": sum(1 for p in plist if p.get("fees")),
            "programmes_with_tabs": sum(1 for p in plist if p.get("tabs")),
            "pages": len(final), "pages_by_kind": dict(collections.Counter(p["kind"] for p in final)),
            "pages_by_category": dict(collections.Counter(p["category"] for p in final)),
            "hosts": dict(collections.Counter(p["host"] for p in final)),
            "external_sources": sorted({p.get("source_label") for p in final if p.get("source_label")}),
            "requests": requests, "seconds": round(time.time() - started)}
    atomic(ROOT / "meta.json", json.dumps(meta, indent=1))
    log(f"SAVED [{phase}{' COMPLETE' if complete else ''}] programmes={meta['programmes']} (elig {meta['programmes_with_eligibility']}, fees {meta['programmes_with_fees']}, tabs {meta['programmes_with_tabs']}) pages={meta['pages']}")
    return meta


async def main(max_pages: int, limit_programmes: int | None = None):
    ROOT.mkdir(parents=True, exist_ok=True)
    f = Fetcher(ROOT / "cache", concurrency=5)
    t0 = time.time()
    pdf_pages: list = []
    ext: list = []
    try:
        crawl = Crawl(f, max_pages)
        ck = lambda phase, complete=False: save(progs, crawl.pages, pdf_pages, ext, phase=phase, complete=complete, started=t0, requests=f.requests)

        log("phase 1: programme catalogue")
        progs = await catalogue(f)
        if limit_programmes:
            progs = dict(list(progs.items())[:limit_programmes])

        async def run_stage(fn, items, label, every=50):
            sem, done = asyncio.Semaphore(4), 0

            async def one(p):
                nonlocal done
                async with sem:
                    await fn(f, p)
                done += 1
                if done % every == 0:
                    log(f"  {label} {done}/{len(items)}  req {f.requests}")

            await asyncio.gather(*[one(p) for p in items])

        log("phase 2: eligibility + fees + overview for every programme (fast, most valuable)")
        await run_stage(course_core, list(progs.values()), "core")
        ck("programmes")

        log("phase 3: crawling the rest of the site (checkpoints every 500 pages)")
        crawl.on_progress = lambda n: ck(f"crawl-{n}")
        await crawl.run([BASE + "/", BASE + "/admission-home", BASE + "/academics", BASE + "/our-programmes/All/1", BASE + "/International-Students"]
                        + [f"https://{h}/" for h in EXT_HOSTS])
        known = {p["code"].rstrip("=") for p in progs.values()}
        new = []
        for code in crawl.course_codes - known:
            cid = unb64(code)
            if cid and cid.isdigit() and cid not in progs:
                progs[cid] = {"id": cid, "code": code, "name": f"Programme {cid}", "level": "other", "campus": None, "application_open": None,
                              "application_close": None, "url": f"{BASE}/courses/{code}", "apply_url": None, "brochure_url": None}
                new.append(progs[cid])
        if new:
            await run_stage(course_core, new, "core(new)")
        log(f"crawl done: {len(crawl.pages)} pages, {len(crawl.pdfs)} pdfs, {len(progs)} programmes")
        ck("crawl-done")

        log("phase 4: programme sections/tabs (slow)")
        for i in range(0, len(progs), 60):  # checkpoint after every batch
            await run_stage(course_tabs, list(progs.values())[i:i + 60], "tabs", every=30)
            ck(f"tabs-{min(i + 60, len(progs))}")

        log("phase 5: PDFs (brochures, fee structures, policies)")
        pdf_urls = dict(crawl.pdfs)
        for p in progs.values():
            for u in ([p["brochure_url"]] if p.get("brochure_url") else []) + p.get("pdfs", []):
                if u and canon(u):
                    pdf_urls.setdefault(u, p["url"])
        pdf_pages.extend(await fetch_pdfs(f, pdf_urls, cap=3000))
        log(f"  extracted text from {len(pdf_pages)} of {len(pdf_urls)} pdfs")

        log("phase 6: external open sources")
        ext.extend(await external(f))
        ck("final", complete=True)
    finally:
        await f.close()


def _running() -> int | None:
    """PID of a live scrape (heartbeat fresh), else None. Also honours runs started before locks existed (fresh scrape.log)."""
    for f in (LOCK, ROOT / "scrape.log"):
        try:
            if time.time() - f.stat().st_mtime < STALE_AFTER_S:
                try:
                    return int(LOCK.read_text().strip() or 0) or -1
                except (OSError, ValueError):
                    return -1
        except OSError:
            continue
    return None


def status() -> int:
    pid = _running()
    print("scraper:", f"RUNNING (pid {pid})" if pid and pid > 0 else "RUNNING" if pid else "not running")
    meta = ROOT / "meta.json"
    if meta.exists():
        m = json.loads(meta.read_text("utf-8"))
        print(f"data   : {'COMPLETE' if m.get('complete') else 'partial'} · phase={m.get('phase')} · programmes={m.get('programmes')} "
              f"(eligibility {m.get('programmes_with_eligibility')}, fees {m.get('programmes_with_fees')}, sections {m.get('programmes_with_tabs')}) · pages={m.get('pages')} · saved {m.get('scraped_at')}")
    else:
        print("data   : nothing saved yet (the first checkpoint is written within a minute of starting)")
    cache = ROOT / "cache"
    print("cache  :", f"{sum(1 for _ in cache.glob('*.gz'))} responses" if cache.exists() else "empty")
    lg = ROOT / "scrape.log"
    if lg.exists():
        print("--- last log lines ---")
        print("\n".join(lg.read_text("utf-8", errors="replace").splitlines()[-6:]))
    return 0


def cli(argv: list[str]) -> int:
    if argv and argv[0] == "status":
        return status()
    ap = argparse.ArgumentParser(prog="scraper", description="Scrape CHRIST public website data into data/christ/")
    ap.add_argument("command", nargs="?", choices=["run", "status"], default="run")
    ap.add_argument("--max-pages", type=int, default=6000)
    ap.add_argument("--limit-programmes", type=int, default=None)
    a = ap.parse_args(argv)
    if a.command == "status":
        return status()
    ROOT.mkdir(parents=True, exist_ok=True)
    other = _running()
    if other:
        print(f"A scrape is already running{f' (pid {other})' if other > 0 else ''}. Two crawlers would double the load on the CHRIST site.\n"
              f"Check it with:  python scrape.py status      (or delete {LOCK} if you are sure it is dead)")
        return 1
    import atexit
    import os
    LOCK.write_text(str(os.getpid()))
    atexit.register(lambda: LOCK.unlink(missing_ok=True))
    asyncio.run(main(a.max_pages, a.limit_programmes))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(cli(sys.argv[1:]))
