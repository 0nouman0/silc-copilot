"""Optional OCR for scanned pages. Needs `pytesseract` + `pdf2image` (and the Tesseract/Poppler binaries); absent by default, in which case
such pages are reported as `needs_ocr` and skipped (never guessed)."""
from __future__ import annotations

from pathlib import Path


def ocr_page(path: Path, page: int) -> str | None:
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_path  # type: ignore
        images = convert_from_path(str(path), first_page=page, last_page=page, dpi=200)
        return (pytesseract.image_to_string(images[0]) or "").strip() or None if images else None
    except Exception:  # noqa: BLE001  library or binary not installed, or the page cannot be rendered
        return None
