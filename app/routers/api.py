from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from ..agent.decision_engine import run_decision_agent
from ..agent.orchestrator import run_agent
from ..applicants.adapter import DEMO_USERS, ApplicantApiUnavailable, get_applicant_api
from ..config import BRAND_PRODUCT, BRAND_UNIVERSITY, agent_mode, integration_mode, llm_config
from ..data.models import SourceRef
from ..data.repository import NotConfigured, get_repository
from ..eligibility import ApplicantProfile, assess
from ..llm import LLMError, get_decider, get_llm_provider

router = APIRouter(prefix="/api")

# ----------------------------------------------------------------- tiny rate limiter (per client IP)
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request, limit: int = 20, window: int = 60):
    key = request.client.host if request.client else "unknown"
    now, q = time.monotonic(), _hits[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, detail={"code": "rate_limited", "message": "Too many requests. Please wait a moment."})
    q.append(now)


# ----------------------------------------------------------------- meta / session
@router.get("/meta")
def meta():
    cfg = llm_config()
    return {"product": BRAND_PRODUCT, "university": BRAND_UNIVERSITY, "mode": integration_mode(),
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


# ----------------------------------------------------------------- applications (authenticated)
@router.get("/applications")
def applications(request: Request):
    uid = auth.current_user_id(request)
    if not uid:
        raise HTTPException(401, detail={"code": "auth_required", "message": "Sign in to see your applications."})
    api = get_applicant_api()
    try:
        apps = api.list_applications(uid)
    except ApplicantApiUnavailable:
        raise HTTPException(503, detail={"code": "live_status_unavailable",
                                         "message": "Live application information cannot be checked right now."})
    return {"demo": api.is_demo, "count": len(apps), "applications": [a.model_dump() for a in apps]}


# ----------------------------------------------------------------- chat
class ChatMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatBody(BaseModel):
    messages: list[ChatMsg] = Field(min_length=1, max_length=24)
    active_application_id: str | None = Field(default=None, max_length=64)


_STATUS = {"not_configured": 503, "rate_limited": 429, "timeout": 504, "unavailable": 502,
           "tools_unsupported": 502, "bad_response": 502}


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
        history = [m.model_dump() for m in body.messages]
        if agent_mode() == "decisions":
            result = await run_decision_agent(get_decider(), history, user_id=uid, active_application=active)
        else:
            result = await run_agent(get_llm_provider(), history, user_id=uid, active_application=active)
    except LLMError as e:
        raise HTTPException(_STATUS.get(e.code, 502), detail={"code": e.code, "message": e.message})
    return {"reply": result.reply, "sources": [s.model_dump() for s in result.sources],
            "tools_used": result.tools_used, "intent": result.intent, "follow_ups": result.follow_ups, "demo": result.demo,
            "active_application_id": active.application_id if active else None}


# ----------------------------------------------------------------- data for non-chat pages
def _repo():
    try:
        return get_repository()
    except NotConfigured:
        raise HTTPException(503, detail={"code": "data_source_unavailable",
                                         "message": "The university data source is not connected."})


@router.get("/programmes")
def programmes(q: str | None = None, level: str | None = None, campus: str | None = None):
    r = _repo()
    return {"demo": r.is_demo, "programmes": [p.model_dump() for p in r.search_programmes(q, level, campus)]}


@router.get("/programmes/{programme_id}")
def programme(programme_id: str):
    r = _repo()
    p = r.get_programme(programme_id)
    if not p:
        raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
    crit, fees = r.get_eligibility(programme_id), r.get_fees(programme_id, "domestic")
    return {"demo": r.is_demo, "programme": p.model_dump(),
            "eligibility": crit.model_dump() if crit else None, "fees": fees.model_dump() if fees else None,
            "dates": [d.model_dump() for d in r.get_dates(programme_id, None)]}


@router.get("/dates")
def dates():
    r = _repo()
    return {"demo": r.is_demo, "dates": [d.model_dump() for d in r.get_dates(None, None)]}


@router.get("/fees/{programme_id}")
def fees(programme_id: str, category: str = "domestic"):
    r = _repo()
    f = r.get_fees(programme_id, category)
    return {"demo": r.is_demo, "fees": f.model_dump() if f else None}


@router.get("/scholarships")
def scholarships():
    r = _repo()
    return {"demo": r.is_demo, "scholarships": [s.model_dump() for s in r.get_scholarships(None)]}


@router.get("/documents")
def documents(applicant_type: str | None = None):
    r = _repo()
    return {"demo": r.is_demo, "documents": [d.model_dump() for d in r.get_documents(applicant_type)]}


@router.get("/campuses")
def campuses():
    r = _repo()
    return {"demo": r.is_demo, "campuses": [c.model_dump() for c in r.get_campuses(None)],
            "hostels": [h.model_dump() for h in r.get_hostel(None)]}


@router.get("/contacts")
def contacts():
    r = _repo()
    return {"demo": r.is_demo, "contact": r.get_contacts().model_dump()}


class EligibilityBody(ApplicantProfile):
    programme_id: str


@router.post("/eligibility")
def eligibility(body: EligibilityBody):
    r = _repo()
    if not r.get_programme(body.programme_id):
        raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
    crit = r.get_eligibility(body.programme_id)
    res = assess(crit, ApplicantProfile(**body.model_dump(exclude={"programme_id"})))
    src: SourceRef | None = r.source(crit.source_doc_id, crit.academic_year) if crit else None
    return {"demo": r.is_demo, "result": res.model_dump(), "source": src.model_dump() if src else None}
