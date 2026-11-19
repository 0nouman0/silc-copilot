"""Structured data sources for the agent. CATALOGUE_SOURCE = mysql | api | none (default: mysql when AGENT_DB_USER is set)."""
from __future__ import annotations

import os

from .api_source import ApiSource
from .base import CatalogueSource, SourceUnavailable
from .mysql_source import MySqlSource

_override: CatalogueSource | None = None
_cached: tuple[tuple, CatalogueSource | None] | None = None


def set_source(src: CatalogueSource | None) -> None:
    """Tests inject a fake here."""
    global _override
    _override = src


def _env_key() -> tuple:
    return tuple(os.getenv(k) for k in ("CATALOGUE_SOURCE", "CATALOGUE_API_URL", "AGENT_DB_USER", "AGENT_DB_PASSWORD",
                                        "APPLICANT_DB_HOST", "APPLICANT_DB_PORT", "APPLICANT_DB_NAME"))


def get_source() -> CatalogueSource | None:
    """One shared instance per configuration (callers cache derived data against the instance)."""
    global _cached
    if _override is not None:
        return _override
    key = _env_key()
    if _cached is None or _cached[0] != key:
        choice = (os.getenv("CATALOGUE_SOURCE") or "").lower()
        src = None if choice == "none" else ApiSource.from_env() if choice == "api" else MySqlSource.from_env()
        _cached = (key, src)
    return _cached[1]


__all__ = ["CatalogueSource", "SourceUnavailable", "get_source", "set_source"]
