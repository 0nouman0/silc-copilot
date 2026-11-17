"""Applicant API adapter (account-specific data).

Ownership is enforced INSIDE the adapter: get_application(user_id, app_id) returns
None for another user's application, so callers cannot leak data by mistake.

Demo mode uses a labelled mock. Production mode requires a real adapter and
raises ApplicantApiUnavailable — it never falls back to demo records.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..config import integration_mode


class ApplicantApiUnavailable(Exception):
    """The live applicant API cannot be reached / is not configured."""


class Application(BaseModel):
    application_id: str
    programme_id: str
    programme_name: str
    campus: str | None = None
    status: str  # exact status string returned by the backend
    offer_status: str | None = None  # only set when the backend explicitly returns it
    last_updated: str | None = None


class ApplicantApi(Protocol):
    is_demo: bool

    def list_applications(self, user_id: str) -> list[Application]: ...
    def get_application(self, user_id: str, application_id: str) -> Application | None: ...


# ---- DEMO MOCK (fictional records; labelled as demo everywhere) ----
DEMO_USERS = {
    "demo-applicant-1": {"name": "Demo Applicant One", "applications": [
        Application(application_id="DEMO-APP-0001", programme_id="mba", programme_name="MBA",
                    campus="Main Campus (demo)", status="Under Review", last_updated="2027-03-02"),
        Application(application_id="DEMO-APP-0002", programme_id="bsc-cs", programme_name="B.Sc. Computer Science",
                    campus="City Campus (demo)", status="Application Submitted", last_updated="2027-02-20"),
        Application(application_id="DEMO-APP-0003", programme_id="msc-ds", programme_name="M.Sc. Data Science",
                    campus="Main Campus (demo)", status="Shortlisted", last_updated="2027-03-10"),
    ]},
    "demo-applicant-2": {"name": "Demo Applicant Two", "applications": [
        Application(application_id="DEMO-APP-0101", programme_id="bcom", programme_name="B.Com.",
                    campus="Main Campus (demo)", status="Shortlisted", offer_status="Offer Released",
                    last_updated="2027-03-12"),
    ]},
}


class DemoApplicantApi:
    is_demo = True

    def list_applications(self, user_id):
        u = DEMO_USERS.get(user_id)
        return list(u["applications"]) if u else []

    def get_application(self, user_id, application_id):
        return next((a for a in self.list_applications(user_id) if a.application_id == application_id), None)


class UnconfiguredApplicantApi:
    is_demo = False

    def list_applications(self, user_id):
        raise ApplicantApiUnavailable("Live applicant API is not configured.")

    def get_application(self, user_id, application_id):
        raise ApplicantApiUnavailable("Live applicant API is not configured.")


_demo = DemoApplicantApi()


def get_applicant_api() -> ApplicantApi:
    return UnconfiguredApplicantApi() if integration_mode() == "production" else _demo
