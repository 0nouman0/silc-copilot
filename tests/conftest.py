import json

import pytest
from fastapi.testclient import TestClient

from app.christ.store import reset_corpus
from app.llm.base import LLMResponse, ToolCall
from app.main import app

BASE = "https://christuniversity.in"


def _prog(pid, name, level, campus, *, elig=None, fees=None, tabs=(), open_="2026-10-15", close="2026-12-06", overview=None):
    code = pid
    return {"id": pid, "code": code, "name": name, "level": level, "campus": campus, "application_open": open_, "application_close": close,
            "url": f"{BASE}/courses/{code}", "apply_url": "https://espro.christuniversity.in/Application/", "brochure_url": None,
            "overview": overview or f"{campus}\n{name}\nProgramme Overview\nAbout the Programme\nThis is the official overview of {name} at CHRIST.",
            "eligibility": elig, "fees": fees, "quick_facts": {"duration": "2 years", "mode": "Day Scholar", "medium": "English", "semesters": 4},
            "tabs": [{"id": str(i), "kind": "common", "title": t, "text": x} for i, (t, x) in enumerate(tabs)]}


FEES_MBA = ("1. Admission Registration Fee: INR 8000/- (Non-Refundable)\n2. Programme Fee (as per the table below)\n"
            "| Year | Karnataka | NRI |\n|---|---|---|\n| 1 | 5,70,000 INR | 6,70,000 INR |")
ELIG_MBA = ("Candidates who have passed a Bachelor's degree with a minimum of 50% marks in aggregate are eligible.\n"
            "Candidates in the final year may apply provisionally.")
PROGRAMMES = [
    _prog("334", "Master of Business Administration (MBA)", "postgraduate", "Bangalore Central Campus", elig=ELIG_MBA, fees=FEES_MBA,
          tabs=[("Selection Process", "Selection is based on the CHRIST entrance test, group discussion and interview."),
                ("Curriculum", "Semester 1: Managerial Economics, Organisational Behaviour.")]),
    _prog("341", "Master of Business Administration (MBA) (Executive)", "postgraduate", "Bangalore Central Campus", fees=FEES_MBA),
    _prog("101", "Bachelor of Business Administration (BBA)", "undergraduate", "Bangalore Central Campus", elig="Pass at the +2 level with 45% marks.", fees=FEES_MBA),
    _prog("102", "Bachelor of Business Administration (BBA)", "undergraduate", "Bangalore Kengeri Campus", elig="Pass at the +2 level with 45% marks.", fees=FEES_MBA),
    _prog("501", "Doctor of Philosophy in Management (PhD)", "doctoral", "Bangalore Central Campus"),
    _prog("201", "Bachelor of Commerce (B.Com.)", "undergraduate", "Bangalore Central Campus", elig="Pass at the +2 level with 50% marks.", fees=FEES_MBA),
]
PAGES = [
    {"url": f"{BASE}/hostel", "host": "christuniversity.in", "title": "Hostel & Dining", "description": "", "kind": "page", "category": "hostel",
     "text": "## Hostel facilities\nCHRIST provides separate hostels for men and women with Wi-Fi, laundry and mess facilities.\n## Hostel registration\nHostel registration opens after admission is confirmed."},
    {"url": f"{BASE}/scholarships", "host": "christuniversity.in", "title": "Scholarships", "description": "", "kind": "page", "category": "scholarships",
     "text": "## Merit scholarships\nCHRIST offers merit scholarships and freeships for deserving students based on published criteria."},
    {"url": f"{BASE}/contact-us", "host": "christuniversity.in", "title": "Contact Us", "description": "", "kind": "page", "category": "contact",
     "text": "## Admissions Office\nWrite to the admissions office or call the helpline listed on this page for enquiries."},
    {"url": f"{BASE}/admission-home", "host": "christuniversity.in", "title": "Admissions", "description": "", "kind": "page", "category": "admissions",
     "text": "## Application process\nApplications are submitted online through the CHRIST application portal. Create an account, fill the form and pay the registration fee.\n## Documents\nKeep your marks cards, photograph and identity proof ready."},
    {"url": f"{BASE}/payment-help", "host": "christuniversity.in", "title": "Online Payment", "description": "", "kind": "page", "category": "fees",
     "text": "## Payment issues\nIf an amount is debited but the payment is not confirmed, contact the payment support team with your transaction reference."},
    {"url": f"{BASE}/about", "host": "christuniversity.in", "title": "About CHRIST", "description": "", "kind": "page", "category": "about",
     "text": "## About\nCHRIST (Deemed to be University) is accredited with an A++ grade by NAAC."},
    {"url": "https://example-blog.test/post", "host": "example-blog.test", "title": "CHRIST blog", "description": "", "kind": "external", "category": "events",
     "source_label": "CHRIST blog", "text": "## Campus life\nStudents celebrated the annual cultural fest on campus."},
]


@pytest.fixture(autouse=True)
def christ_data(tmp_path, monkeypatch):
    (tmp_path / "programmes.json").write_text(json.dumps(PROGRAMMES), "utf-8")
    (tmp_path / "pages.jsonl").write_text("\n".join(json.dumps(p) for p in PAGES), "utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"scraped_at": "2026-11-17T10:00:00+00:00", "source_site": BASE}), "utf-8")
    monkeypatch.setenv("CHRIST_DATA_DIR", str(tmp_path))
    reset_corpus()
    yield
    reset_corpus()


class FakeProvider:
    """Scripted chat LLM (tools mode)."""
    name, model = "fake", "fake-model"

    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def complete(self, messages, tools=None, temperature=0.2):
        self.seen.append((messages, tools))
        return self.script.pop(0)


def call(name, **args):
    return LLMResponse(None, [ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(args))])


def text(t):
    return LLMResponse(t, [])


@pytest.fixture(autouse=True)
def demo_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "demo")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for k in ("LLM_MODEL", "AGENT_MODE", "LLM_PROVIDER", "LLM_BASE_URL", "LLM_DECISIONS_URL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def login(client):
    def _login(uid):
        r = client.post("/api/session/demo-login", json={"user_id": uid})
        assert r.status_code == 200
        return client
    return _login
