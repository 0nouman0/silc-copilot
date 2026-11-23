"""Offline PDF pipeline for the catalog's ~350 document references.

    python -m app.documents.build [--no-download] [--no-ocr] [--limit N]

For each programme document: use a local file (data/documents/files/) or download it, skip it if its hash is unchanged, extract text with
page numbers, drop pages about fees or dates, and cache the result under data/documents/extracted/. Then refresh the catalog
(POST /admin/catalog/refresh) to attach the text. Extraction output is also what you commit to the repo when servers cannot write to disk.
"""
from __future__ import annotations

import json
import sys

from .cache import cached_hash, save_extracted
from .extract import extract_pdf
from .fetch import fetch_file, local_path, sha256_of


def main(argv: list[str]) -> int:
    from ..catalog.refresh import get_catalog
    no_dl, no_ocr = "--no-download" in argv, "--no-ocr" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    cat = get_catalog()
    seen: set[str] = set()
    stats = {"documents": 0, "extracted": 0, "unchanged": 0, "missing": 0, "not_pdf": 0, "needs_ocr": 0, "failed": 0, "dropped_pages": 0}
    for p in cat.programs.values():
        for d in p.documents:
            if d.filename in seen:
                continue
            seen.add(d.filename)
            if limit is not None and len(seen) > limit:
                break
            stats["documents"] += 1
            if not d.filename.lower().endswith(".pdf"):
                stats["not_pdf"] += 1
                continue
            path = local_path(d.filename) if local_path(d.filename).exists() else (None if no_dl else fetch_file(d.url, d.filename))
            if path is None:
                stats["missing"] += 1
                continue
            sha = sha256_of(path)
            if cached_hash(d.filename) == sha:
                stats["unchanged"] += 1
                continue
            rep = extract_pdf(path, ocr=not no_ocr)
            if rep["error"]:
                stats["failed"] += 1
                continue
            save_extracted(d.filename, sha, d.kind, rep["sections"])
            stats["extracted"] += 1
            stats["dropped_pages"] += len(rep["dropped_pages"])
            stats["needs_ocr"] += 1 if rep["needs_ocr"] else 0
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    sys.exit(main(sys.argv[1:]))
