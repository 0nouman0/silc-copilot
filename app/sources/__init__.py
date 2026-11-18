"""Structured data sources for the agent. CATALOGUE_SOURCE = mysql | api | none (default: mysql when AGENT_DB_USER is set)."""
from __future__ import annotations

import os

from .api_source import ApiSource
from .base import CatalogueSource, SourceUnavailable
from .mysql_source import MySqlSource

_override: CatalogueSource | None = None


def set_source(src: CatalogueSource | None) -> None:
    """Tests inject a fake here."""
    global _override
    _override = src


def get_source() -> CatalogueSource | None:
    if _override is not None:
        return _override
    choice = os.getenv("CATALOGUE_SOURCE", "").lower()
    if choice == "none":
        return None
    if choice == "api":
        return ApiSource.from_env()
    return MySqlSource.from_env()


__all__ = ["CatalogueSource", "SourceUnavailable", "get_source", "set_source"]
