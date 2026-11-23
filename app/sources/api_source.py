"""Placeholder for the future HTTP API that will replace direct DB access.

Implement the `CatalogueSource` methods here (each is one HTTP call returning the same dict shapes as
`MySqlSource`) and set CATALOGUE_SOURCE=api. Nothing else in the agent changes. Until then it reports itself
as unavailable instead of guessing.
"""
from __future__ import annotations

import os

from .base import SourceUnavailable


class ApiSource:
    name = "api"

    def __init__(self, base_url: str, token: str | None):
        self.base_url, self.token = base_url.rstrip("/"), token

    @classmethod
    def from_env(cls) -> "ApiSource | None":
        url = os.getenv("CATALOGUE_API_URL")
        return cls(url, os.getenv("CATALOGUE_API_TOKEN")) if url else None

    def _nope(self, *a, **k):
        raise SourceUnavailable("catalogue API adapter is not implemented yet")

    list_campus_details = list_facilities = list_catalogue = search_courses = get_course = get_sections = get_highlights = get_curriculum = get_roadmap = get_admission_process = list_campuses = _nope

    def ping(self) -> bool:
        return False
