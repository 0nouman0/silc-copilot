"""Build the compact deployment bundle (deploy_data/christ/*.gz) from the scrape output.

    python scrape.py bundle                 # default budgets
    python scrape.py bundle --pdf-chars 20000 --max-pdf-mb 15

Serverless hosts (Vercel) deploy from git and have no scrape folder, so the app falls back to this bundle.
Everything in it is public CHRIST website content; it is only compressed and trimmed:
  * every programme (eligibility, fees, sections) and every page/external page are kept,
  * PDFs are kept only for admission-relevant categories, each truncated, within a total size budget
    (exam timetables, notices and the like are dropped).
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from .run import ROOT as SCRAPE_ROOT

OUT = SCRAPE_ROOT.parent.parent / "deploy_data" / "christ"
PDF_PRIORITY = ["admissions", "fees", "scholarships", "international", "hostel", "placements", "programmes", "campus", "about", "contact"]
DROP_FIELDS = ("description", "depth", "fetched_at")


def _gz_write(path: Path, text: str) -> int:
    data = gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)  # mtime=0: identical input => identical bytes (clean git diffs)
    path.write_bytes(data)
    return len(data)


def build(src: Path = SCRAPE_ROOT, out: Path = OUT, pdf_chars: int = 30_000, max_pdf_mb: float = 25.0) -> dict:
    prog_file, pages_file = src / "programmes.json", src / "pages.jsonl"
    if not prog_file.exists():
        raise SystemExit(f"No scrape output in {src}. Run: python scrape.py")
    progs = json.loads(prog_file.read_text("utf-8"))
    pages = [json.loads(ln) for ln in pages_file.read_text("utf-8").split("\n") if ln.strip()] if pages_file.exists() else []  # not splitlines(): see store.py

    kept, pdfs = [], []
    for p in pages:
        for f in DROP_FIELDS:
            p.pop(f, None)
        (pdfs if p["kind"] == "pdf" else kept).append(p)

    budget, used, kept_pdfs = int(max_pdf_mb * 1_000_000), 0, 0
    rank = {c: i for i, c in enumerate(PDF_PRIORITY)}
    for p in sorted((x for x in pdfs if x.get("category") in rank), key=lambda x: (rank[x["category"]], -len(x["text"]))):
        p["text"] = p["text"][:pdf_chars]
        if used + len(p["text"]) > budget:
            continue
        used += len(p["text"])
        kept.append(p)
        kept_pdfs += 1

    out.mkdir(parents=True, exist_ok=True)
    sizes = {"programmes.json.gz": _gz_write(out / "programmes.json.gz", json.dumps(progs, ensure_ascii=False, separators=(",", ":"))),
             "pages.jsonl.gz": _gz_write(out / "pages.jsonl.gz", "".join(json.dumps(p, ensure_ascii=False, separators=(",", ":")) + "\n" for p in kept))}
    meta = json.loads((src / "meta.json").read_text("utf-8")) if (src / "meta.json").exists() else {}
    meta["bundle"] = {"pages": len(kept), "pdfs_kept": kept_pdfs, "pdfs_dropped": len(pdfs) - kept_pdfs, "pdf_chars_each": pdf_chars}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), "utf-8")
    return {"out": str(out), "programmes": len(progs), "pages": len(kept), "pdfs_kept": kept_pdfs, "pdfs_dropped": len(pdfs) - kept_pdfs,
            "complete": meta.get("complete"), "compressed_mb": round(sum(sizes.values()) / 1e6, 2)}


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="scraper bundle")
    ap.add_argument("--pdf-chars", type=int, default=30_000)
    ap.add_argument("--max-pdf-mb", type=float, default=25.0)
    a = ap.parse_args(argv)
    r = build(pdf_chars=a.pdf_chars, max_pdf_mb=a.max_pdf_mb)
    print(f"bundle written to {r['out']}")
    print(f"  programmes={r['programmes']}  pages={r['pages']} (PDFs kept {r['pdfs_kept']}, dropped {r['pdfs_dropped']})  compressed={r['compressed_mb']} MB")
    if not r["complete"]:
        print("  NOTE: the scrape is not complete, so this bundle is partial. Re-run `python scrape.py bundle` after it finishes.")
    return 0
