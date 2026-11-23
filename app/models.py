from __future__ import annotations

from pydantic import BaseModel


class SourceRef(BaseModel):
    """Where a fact came from: a record in the CHRIST programme database (no URL)."""
    doc_id: str
    title: str
    url: str | None = None
    academic_year: str | None = None
    external: bool = False
