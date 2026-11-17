"""Repository abstraction. Tools depend on this interface, never on a vendor DB.

To connect the institution's real source (MySQL, REST, ...) implement
AdmissionsRepository and return it from get_repository() for production mode.
"""
from __future__ import annotations

from typing import Protocol

from ..config import integration_mode
from . import demo_data as D
from .models import (Campus, ContactInfo, EligibilityCriteria, FeeStructure, HostelInfo, ImportantDate,
                     PlacementReport, Programme, RequiredDocument, Scholarship, SourceRef)


class NotConfigured(Exception):
    """Raised when a real data source is required but not wired up."""


class AdmissionsRepository(Protocol):
    is_demo: bool

    def search_programmes(self, query: str | None, level: str | None, campus_id: str | None) -> list[Programme]: ...
    def get_programme(self, programme_id: str) -> Programme | None: ...
    def get_eligibility(self, programme_id: str) -> EligibilityCriteria | None: ...
    def get_dates(self, programme_id: str | None, applicant_type: str | None) -> list[ImportantDate]: ...
    def get_fees(self, programme_id: str, category: str) -> FeeStructure | None: ...
    def get_scholarships(self, level: str | None) -> list[Scholarship]: ...
    def get_documents(self, applicant_type: str | None) -> list[RequiredDocument]: ...
    def get_campuses(self, campus_id: str | None) -> list[Campus]: ...
    def get_hostel(self, campus_id: str | None) -> list[HostelInfo]: ...
    def get_placements(self, programme_id: str | None) -> list[PlacementReport]: ...
    def get_contacts(self) -> ContactInfo: ...
    def source(self, doc_id: str, year: str | None = None) -> SourceRef: ...


class DemoRepository:
    is_demo = True

    def search_programmes(self, query, level, campus_id):
        q = (query or "").lower().strip()
        out = []
        for p in D.PROGRAMMES:
            if level and p.level != level:
                continue
            if campus_id and campus_id not in p.campus_ids:
                continue
            hay = f"{p.id} {p.name} {p.discipline} {' '.join(p.specializations)}".lower()
            if q and not any(tok in hay for tok in q.replace(".", " ").split()):
                continue
            out.append(p)
        return out

    def get_programme(self, programme_id):
        return next((p for p in D.PROGRAMMES if p.id == programme_id), None)

    def get_eligibility(self, programme_id):
        return next((e for e in D.ELIGIBILITY if e.programme_id == programme_id), None)

    def get_dates(self, programme_id, applicant_type):
        return [d for d in D.DATES
                if (d.programme_id in (None, programme_id))
                and (not applicant_type or d.applicant_type in ("all", applicant_type))]

    def get_fees(self, programme_id, category):
        return next((f for f in D.FEES if f.programme_id == programme_id and f.applicant_category == category), None)

    def get_scholarships(self, level):
        return [s for s in D.SCHOLARSHIPS if not level or level in s.applicable_levels]

    def get_documents(self, applicant_type):
        return [d for d in D.DOCUMENTS
                if not applicant_type or "all" in d.applies_to or applicant_type in d.applies_to]

    def get_campuses(self, campus_id):
        return [c for c in D.CAMPUSES if not campus_id or c.id == campus_id]

    def get_hostel(self, campus_id):
        return [h for h in D.HOSTELS if not campus_id or h.campus_id == campus_id]

    def get_placements(self, programme_id):
        return [p for p in D.PLACEMENTS if not programme_id or p.programme_id == programme_id]

    def get_contacts(self):
        return D.CONTACTS

    def source(self, doc_id, year=None):
        return SourceRef(doc_id=doc_id, title=D.DOC_TITLES.get(doc_id, doc_id), demo=True, academic_year=year)


_demo = DemoRepository()


def get_repository() -> AdmissionsRepository:
    if integration_mode() == "production":
        # Never silently fall back to demo data in production.
        raise NotConfigured("Production data adapter is not configured. See README 'Integration contracts'.")
    return _demo
