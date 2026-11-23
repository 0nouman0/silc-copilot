"""In-memory catalog built from the read-only website replica (see docs/ARCHITECTURE.md)."""
from .builder import CatalogCheckFailed, build_catalog, check_catalog
from .models import Catalog, SectionKind
from .refresh import current_catalog, get_catalog, maybe_refresh, refresh, set_catalog, set_reader, version

__all__ = ["Catalog", "SectionKind", "CatalogCheckFailed", "build_catalog", "check_catalog", "current_catalog", "get_catalog", "maybe_refresh",
           "refresh", "set_catalog", "set_reader", "version"]
