from __future__ import annotations

from pydantic import BaseModel, Field


class Tab(BaseModel):
    id: str
    kind: str
    title: str
    text: str


class Programme(BaseModel):
    id: str
    code: str
    name: str
    level: str  # undergraduate | postgraduate | doctoral | other
    campus: str | None = None
    application_open: str | None = None  # ISO date from the catalogue
    application_close: str | None = None
    url: str
    apply_url: str | None = None
    brochure_url: str | None = None
    overview: str | None = None
    eligibility: str | None = None
    fees: str | None = None
    quick_facts: dict = Field(default_factory=dict)
    tabs: list[Tab] = Field(default_factory=list)


class SourceRef(BaseModel):
    """Where a fact came from. `url` is the public page on christuniversity.in (or other listed source)."""
    doc_id: str
    title: str
    url: str | None = None
    demo: bool = False  # only True for the mock applicant API
    academic_year: str | None = None
    external: bool = False
