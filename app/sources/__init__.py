"""Structured data sources for the agent. CATALOGUE_SOURCE = catalog | mysql | api | none.

catalog (default when AGENT_DB_USER is set): the in-memory catalog built from the read-only replica (no per-request SQL).
mysql: legacy per-request queries. api: HTTP adapter stub. none: no source (the app says the database cannot be reached)."""
from __future__ import annotations

import os

from .api_source import ApiSource
from .base import CatalogueSource, DataNotGranted, SourceUnavailable
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
        if choice == "none":
            src = None
        elif choice == "api":
            src = ApiSource.from_env()
        elif choice == "mysql":
            src = MySqlSource.from_env()
        else:                                     # "catalog" or unset: the blueprint's in-memory catalog, when a database account is configured
            from .catalog_source import CatalogSource   # lazy: the catalog package itself imports app.sources.base
            src = CatalogSource() if (os.getenv("AGENT_DB_USER") and os.getenv("AGENT_DB_PASSWORD")) or choice == "catalog" else None
        _cached = (key, src)
    return _cached[1]


__all__ = ["CatalogueSource", "DataNotGranted", "SourceUnavailable", "get_source", "set_source"]
