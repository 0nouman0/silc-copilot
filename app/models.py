from __future__ import annotations

from pydantic import BaseModel


class SourceRef(BaseModel):
    """Where a fact came from: a record in the CHRIST programme database (no URL), or the demo applicant API."""
    doc_id: str
    title: str
    url: str | None = None
    demo: bool = False
    academic_year: str | None = None
    external: bool = False
