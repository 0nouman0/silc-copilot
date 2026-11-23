"""Get a document into `data/documents/files/`: a file the web team dropped there wins, else download it once from the website.

Hash-based: a file whose content hash is unchanged is not extracted again. Only plain http(s) URLs of the programme documents the catalog
lists are fetched (never a URL from user or model text).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .cache import FILES, _name

MAX_BYTES = 40 * 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_path(filename: str) -> Path:
    return FILES / _name(filename)


def fetch_file(url: str, filename: str, timeout: float = 30.0) -> Path | None:
    """Local copy of the document, downloading it if no local file exists. None when it cannot be had (reported, never fatal)."""
    dest = local_path(filename)
    if dest.exists():
        return dest
    if urlparse(url).scheme not in ("http", "https"):
        return None
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            if r.status_code != 200:
                return None
            FILES.mkdir(parents=True, exist_ok=True)
            size = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        dest.unlink(missing_ok=True)
                        return None
                    f.write(chunk)
    except (httpx.HTTPError, OSError):
        dest.unlink(missing_ok=True)
        return None
    return dest
