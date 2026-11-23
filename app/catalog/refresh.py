"""Catalog lifecycle: build at startup, manual refresh, checksum safety net, atomic swap.

Students keep using the old catalog until the new one passes every check; then one reference assignment (atomic in Python) makes
it live. The catalog is immutable, so readers need no locks. Each app server holds its own copy, so the refresh must be called on
every instance (the admin script holds the list); each server also runs `maybe_refresh` on a timer as a safety net.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..sources.base import SourceUnavailable
from .builder import CatalogCheckFailed, build_catalog
from .config import load_config
from .models import Catalog
from .reader import MySqlReader, RowReader
from .snapshot import diff_programs, write_snapshot

log = logging.getLogger("catalog")

MAX_PROGRAM_DROP = 0.20      # refuse the swap if programmes drop by more than 20%
MAX_SECTION_DROP = 0.30      # ... or sections by more than 30%

_current: Catalog | None = None
_reader_override: RowReader | None = None
_build_lock = threading.Lock()
_swap_version = 0


def set_reader(r: RowReader | None) -> None:
    """Tests inject a fake reader."""
    global _reader_override
    _reader_override = r


def get_reader() -> RowReader | None:
    if _reader_override is not None:
        return _reader_override
    choice = (os.getenv("CATALOGUE_SOURCE") or "").lower()
    if choice in ("none", "api"):
        return None
    return MySqlReader.from_env()


def set_catalog(c: Catalog | None) -> None:
    global _current, _swap_version
    _current = c
    _swap_version += 1


def current_catalog() -> Catalog | None:
    return _current


def version() -> int:
    """Changes on every swap, so derived caches can invalidate."""
    return _swap_version


def get_catalog() -> Catalog:
    """The live catalog. Builds it on first use when a reader is configured; raises SourceUnavailable otherwise."""
    if _current is None:
        refresh(force=True, who="first-use")
    if _current is None:
        raise SourceUnavailable("the catalog has not been built")
    return _current


@dataclass
class RefreshReport:
    swapped: bool
    reason: str
    added: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    seconds: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    who: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"swapped": self.swapped, "reason": self.reason, "programmes_added": len(self.added), "programmes_updated": len(self.updated),
                "programmes_removed": len(self.removed), "build_seconds": round(self.seconds, 2), "counts": self.counts, "warnings": self.warnings, "triggered_by": self.who}


def sanity(old: Catalog | None, new: Catalog) -> list[str]:
    """Reasons to refuse the swap."""
    errs: list[str] = []
    if not new.programs:
        errs.append("the new catalog has no programmes")
    if old is not None:
        op, np_ = len(old.programs), len(new.programs)
        if op and (op - np_) / op > MAX_PROGRAM_DROP:
            errs.append(f"programmes dropped from {op} to {np_} (more than {int(MAX_PROGRAM_DROP * 100)}%)")
        os_, ns = old.build.counts.get("sections", 0), new.build.counts.get("sections", 0)
        if os_ and (os_ - ns) / os_ > MAX_SECTION_DROP:
            errs.append(f"sections dropped from {os_} to {ns} (more than {int(MAX_SECTION_DROP * 100)}%)")
    return errs


def refresh(force: bool = False, who: str = "system", reader: RowReader | None = None, extracted: dict | None = None) -> RefreshReport:
    """Build a new catalog beside the old one and swap it in if it passes the checks. Skips when no table changed (unless force)."""
    rd = reader or get_reader()
    if rd is None:
        raise SourceUnavailable("no database reader is configured")
    with _build_lock:
        t0 = datetime.now(timezone.utc)
        old = _current
        if not force and old is not None:
            try:
                if rd.checksums() == dict(old.build.source_checksums):
                    return RefreshReport(False, "no source table changed", who=who)
            except Exception:  # noqa: BLE001  cannot compare: fall through to a rebuild
                pass
        try:
            new = build_catalog(rd, load_config(), extracted=extracted if extracted is not None else _extracted())
        except CatalogCheckFailed as e:
            log.error("catalog build refused: %s", e)
            return RefreshReport(False, f"build check failed: {e}", who=who, warnings=e.problems)
        errs = sanity(old, new)
        secs = (datetime.now(timezone.utc) - t0).total_seconds()
        if errs:
            log.error("catalog swap refused: %s", errs)
            return RefreshReport(False, "swap refused: " + "; ".join(errs), seconds=secs, who=who, warnings=list(new.build.warnings))
        added, updated, removed = diff_programs(old, new)
        set_catalog(new)
        log.info("catalog swapped by %s: %s", who, new.build.counts)
        snap = os.getenv("CATALOG_SNAPSHOT")
        if snap:
            try:
                write_snapshot(new, snap)
            except OSError as e:
                log.warning("snapshot not written: %s", e)
        return RefreshReport(True, "swapped", added, updated, removed, secs, dict(new.build.counts), list(new.build.warnings), who)


def _extracted() -> dict:
    try:
        from ..documents.cache import load_extracted
        return load_extracted()
    except Exception:  # noqa: BLE001  no PDF cache yet
        return {}


def maybe_refresh(who: str = "timer") -> RefreshReport | None:
    """Safety net run every few minutes: rebuild only when a source table checksum changed."""
    try:
        return refresh(force=False, who=who)
    except SourceUnavailable:
        return None
