"""PDF text extraction: text layer first (pdfplumber if installed, else pypdf), OCR only for pages with no text, pages about fees or dates dropped."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# A page is dropped when it is about fees or dates: 2+ hits anywhere, or one hit in its first lines (its heading).
_FEE_DATE = re.compile(r"\b(fees?|tuition|caution deposit|application (opens?|closes?|dates?)|last date|important dates?|deadline|payable)\b", re.I)
MIN_TEXT = 40     # fewer characters than this on a page = no text layer (scanned)


def about_fees_or_dates(text: str) -> bool:
    head = "\n".join(text.split("\n")[:3])
    return len(_FEE_DATE.findall(text)) >= 2 or bool(_FEE_DATE.search(head))


def _pages_text(path: Path) -> list[str]:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(path)) as pdf:
            out = []
            for pg in pdf.pages:
                txt = pg.extract_text() or ""
                for t in pg.extract_tables() or []:        # keep tables as tables (Markdown rows)
                    rows = [" | ".join((c or "").strip() for c in r) for r in t if any(r)]
                    if rows:
                        txt += "\n" + "\n".join(f"| {r} |" for r in rows)
                out.append(txt)
            return out
    except ImportError:
        pass
    from pypdf import PdfReader
    return [(pg.extract_text() or "") for pg in PdfReader(str(path)).pages]


def extract_pdf(path: Path, ocr: bool = True) -> dict[str, Any]:
    """{'sections': [{'page', 'text'}], 'dropped_pages': [...], 'ocr_pages': [...], 'needs_ocr': [...], 'pages': n}. Never raises on a bad file."""
    report: dict[str, Any] = {"sections": [], "dropped_pages": [], "ocr_pages": [], "needs_ocr": [], "pages": 0, "error": None}
    try:
        pages = _pages_text(path)
    except Exception as e:  # noqa: BLE001  corrupt or encrypted file
        report["error"] = f"{type(e).__name__}: {e}"[:200]
        return report
    report["pages"] = len(pages)
    for i, raw in enumerate(pages, start=1):
        text = re.sub(r"[ \t]+", " ", raw or "").strip()
        if len(text) < MIN_TEXT:
            got = None
            if ocr:
                from .ocr import ocr_page
                got = ocr_page(path, i)
            if got:
                text = got
                report["ocr_pages"].append(i)
            else:
                report["needs_ocr"].append(i)
                continue
        if about_fees_or_dates(text):
            report["dropped_pages"].append(i)
            continue
        report["sections"].append({"page": i, "text": text})
    return report
