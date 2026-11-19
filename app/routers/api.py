from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from .. import auth
from ..agent.decision_engine import run_decision_agent
from ..agent.orchestrator import run_agent
from ..applicants.adapter import DEMO_USERS, ApplicantApiUnavailable, get_applicant_api
from ..catalogue import get_catalogue
from ..config import BRAND_PRODUCT, BRAND_UNIVERSITY, agent_mode, integration_mode, llm_config
from ..eligibility_hint import hint
from ..llm import LLMError, get_composer, get_decider, get_llm_provider
from ..sources import SourceUnavailable, get_source
from ..sources.text import tidy

router = APIRouter(prefix="/api")

# ----------------------------------------------------------------- tiny rate limiter (per client IP)
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request, window: int = 60):
    limit = int(os.getenv("CHAT_RATE_LIMIT_PER_MIN", "20"))
    key = request.client.host if request.client else "unknown"
    now, q = time.monotonic(), _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many requests. Please wait a moment."})
    q.append(now)


def _unavailable() -> HTTPException:
    return HTTPException(503, detail={"code": "data_unavailable", "message": "The CHRIST programme database cannot be reached right now. Please try again shortly."})


def _catalogue():
    try:
        return get_catalogue()
    except SourceUnavailable:
        raise _unavailable()


def _db():
    src = get_source()
    if src is None:
        raise _unavailable()
    return src


# ----------------------------------------------------------------- meta / session
@router.get("/meta")
def meta():
    cfg = llm_config()
    try:
        data = {"ready": True, **get_catalogue().stats()}
    except SourceUnavailable:
        data = {"ready": False, "message": "The CHRIST programme database cannot be reached."}
    return {"product": BRAND_PRODUCT, "university": BRAND_UNIVERSITY, "mode": integration_mode(), "data": data,
            "llm": {"provider": cfg.provider, "model": cfg.model, "configured": bool(cfg.api_key), "agent_mode": agent_mode()},
            "demo_login": auth.demo_login_allowed()}


@router.get("/session")
def session(request: Request):
    uid = auth.current_user_id(request)
    user = DEMO_USERS.get(uid or "")
    return {"authenticated": bool(user), "user": {"id": uid, "name": user["name"]} if user else None,
            "demo_users": [{"id": k, "name": v["name"]} for k, v in DEMO_USERS.items()] if auth.demo_login_allowed() else []}


class LoginBody(BaseModel):
    user_id: str


@router.post("/session/demo-login")
def demo_login(body: LoginBody, response: Response):
    if not auth.demo_login_allowed() or body.user_id not in DEMO_USERS:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Demo sign-in is not available."})
    response.set_cookie(auth.COOKIE, auth.sign(body.user_id), httponly=True, samesite="lax", max_age=8 * 3600)
    return {"ok": True}


@router.post("/session/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


# ----------------------------------------------------------------- applications (authenticated; mock applicant API)
@router.get("/applications")
def applications(request: Request):
    uid = auth.current_user_id(request)
    if not uid:
        raise HTTPException(401, detail={"code": "auth_required", "message": "Sign in to see your applications."})
    api = get_applicant_api()
    try:
        apps = api.list_applications(uid)
    except ApplicantApiUnavailable:
        raise HTTPException(503, detail={"code": "live_status_unavailable", "message": "Live application information cannot be checked right now."})
    return {"demo": api.is_demo, "count": len(apps), "applications": [a.model_dump() for a in apps]}


# ----------------------------------------------------------------- chat
USER_MAX = 4000        # what a person may type
HISTORY_MAX = 20000    # assistant replies echoed back as history (fee tables are long); trimmed before use
HISTORY_KEEP = 3000


class ChatMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=HISTORY_MAX)

    @model_validator(mode="after")
    def _user_limit(self):
        if self.role == "user" and len(self.content) > USER_MAX:
            raise ValueError(f"Message is too long (max {USER_MAX} characters).")
        return self


class ChatBody(BaseModel):
    messages: list[ChatMsg] = Field(min_length=1, max_length=24)
    active_application_id: str | None = Field(default=None, max_length=64)


_STATUS = {"not_configured": 503, "rate_limited": 429, "timeout": 504, "unavailable": 502, "tools_unsupported": 502, "bad_response": 502}


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    _rate_limit(request)
    if body.messages[-1].role != "user":
        raise HTTPException(422, detail={"code": "invalid", "message": "The last message must be from the user."})
    uid = auth.current_user_id(request)

    # The active application is trusted ONLY after the server verifies ownership.
    active = None
    if uid and body.active_application_id:
        try:
            active = get_applicant_api().get_application(uid, body.active_application_id)
        except ApplicantApiUnavailable:
            active = None

    try:
        history = [{"role": m.role, "content": m.content if m.role == "user" else m.content[:HISTORY_KEEP]} for m in body.messages]
        if agent_mode() == "decisions":
            result = await run_decision_agent(get_decider(), history, user_id=uid, active_application=active, composer=get_composer())
        else:
            result = await run_agent(get_llm_provider(), history, user_id=uid, active_application=active)
    except LLMError as e:
        raise HTTPException(_STATUS.get(e.code, 502), detail={"code": e.code, "message": e.message})
    return {"reply": result.reply, "sources": [s.model_dump() for s in result.sources], "tools_used": result.tools_used,
            "intent": result.intent, "follow_ups": result.follow_ups, "demo": result.demo,
            "active_application_id": active.application_id if active else None}


# ----------------------------------------------------------------- data for non-chat pages (CHRIST programme database)
DB_SOURCE = "CHRIST programme database"


def _light(p):
    return {"id": p.id, "name": p.name, "level": p.level, "campus": p.campus, "department": p.department, "duration_years": p.duration_years,
            "application_open": p.application_open, "application_close": p.application_close}


@router.get("/programmes")
def programmes(q: str | None = None, level: str | None = None, campus: str | None = None, offset: int = 0, limit: int = 60):
    c = _catalogue()
    rows = c.search(q, level, campus, limit=10_000)
    limit = max(1, min(limit, 500))
    return {"total": len(rows), "offset": offset, "programmes": [_light(p) for p in rows[offset:offset + limit]],
            "campuses": c.campuses(), "source": DB_SOURCE}


def _sections_text(rows: list[dict]) -> str | None:
    if not rows:
        return None
    if len(rows) == 1:
        return tidy(rows[0]["text"])
    return "\n\n".join(f"#### {r['section']}\n\n{tidy(r['text'])}" for r in rows)


def _curriculum_md(curriculum: list[dict]) -> str:
    parts = []
    for t in curriculum:
        head = f"#### {t['year']} · Term {t['term']}" + (f" — {t['focus']}" if t.get("focus") else "")
        rows = "\n".join(f"| {s['subject'].replace('|', '/')} | {s['category'] or ''} | {s['credits'] if s['credits'] is not None else ''} |" for s in t["subjects"])
        parts.append(head + ("\n\n| Subject | Category | Credits |\n|---|---|---|\n" + rows if rows else ""))
    return "\n\n".join(parts)


HIGHLIGHT_TITLES = {"why_choose": "Why choose this programme", "what_you_will_learn": "What you will learn", "careers": "Career paths", "modules": "Modules"}


@router.get("/programmes/{programme_id}")
def programme(programme_id: int):
    p = _catalogue().by_id.get(programme_id)
    src = _db()
    try:
        rec = src.get_course(programme_id) if p else None
        if not p or not rec:
            raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
        eligibility = _sections_text(src.get_sections(programme_id, "eligibility")[:3])
        fees = _sections_text(src.get_sections(programme_id, "fees")[:2])
        other = [s for s in src.get_sections(programme_id, None) if not s["section"].lower().startswith(("eligib", "fee"))]
        highlights = src.get_highlights(programme_id)
        curriculum = src.get_curriculum(programme_id, None)
    except SourceUnavailable:
        raise _unavailable()
    tabs = []
    if rec.get("selection_process"):
        tabs.append({"id": "selection", "title": "Selection process", "text": tidy(rec["selection_process"])})
    if curriculum:
        tabs.append({"id": "curriculum", "title": "Curriculum", "text": _curriculum_md(curriculum)})
    for kind, title in HIGHLIGHT_TITLES.items():
        items = [h for h in highlights if h["kind"] == kind]
        if items:
            tabs.append({"id": kind, "title": title, "text": "\n".join(f"- **{h['title']}**" + (f": {h['text']}" if h["text"] else "") for h in items)})
    tabs += [{"id": f"s{i}", "title": s["section"], "text": tidy(s["text"])} for i, s in enumerate(other)]
    return {"programme": {**_light(p), "code": rec.get("code"), "term_type": rec.get("term_type"), "approved_intake": rec.get("approved_intake"),
                          "level_label": p.level_label, "summary": rec.get("summary"), "overview": rec.get("overview"),
                          "record_last_modified": rec.get("record_last_modified"), "eligibility": eligibility, "fees": fees, "tabs": tabs},
            "source": DB_SOURCE}


@router.get("/dates")
def dates():
    c = _catalogue()
    return {"windows": [{"level": lv, "campus": cp, "open": o, "close": cl, "programmes": n} for (lv, cp, o, cl, n) in c.windows()], "source": DB_SOURCE,
            "note": "Dates are the ones stored on each programme record in the database and may be out of date. Confirm with the Admissions Office."}


@router.get("/admission-process")
def admission_process():
    try:
        rows = _db().get_admission_process(None)
    except SourceUnavailable:
        raise _unavailable()
    return {"rows": [{"group": r["group"], **{k: tidy(v) for k, v in r.items() if k != "group"}} for r in rows], "source": DB_SOURCE}


@router.get("/campuses")
def campuses():
    try:
        rows = _db().list_campuses()
    except SourceUnavailable:
        raise _unavailable()
    return {"campuses": rows, "source": DB_SOURCE}


class EligibilityBody(BaseModel):
    programme_id: int
    percentage: float | None = Field(default=None, ge=0, le=100)


@router.post("/eligibility")
def eligibility(body: EligibilityBody):
    p = _catalogue().by_id.get(body.programme_id)
    if not p:
        raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
    try:
        rows = _db().get_sections(body.programme_id, "eligibility")[:3]
    except SourceUnavailable:
        raise _unavailable()
    raw = "\n".join(r["text"] for r in rows) or None
    return {"programme": {"id": p.id, "name": p.name, "campus": p.campus}, "eligibility": _sections_text(rows), "result": hint(raw, body.percentage), "source": DB_SOURCE}
