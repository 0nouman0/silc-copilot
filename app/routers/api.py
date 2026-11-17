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
from ..christ.eligibility_hint import hint
from ..christ.format import prose
from ..christ.store import DataNotReady, get_corpus
from ..config import BRAND_PRODUCT, BRAND_UNIVERSITY, agent_mode, integration_mode, llm_config
from ..llm import LLMError, get_decider, get_llm_provider

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


def _corpus():
    try:
        return get_corpus()
    except DataNotReady as e:
        raise HTTPException(503, detail={"code": "data_not_ready", "message": str(e)})


# ----------------------------------------------------------------- meta / session
@router.get("/meta")
def meta():
    cfg = llm_config()
    try:
        data = {"ready": True, **get_corpus().stats()}
    except DataNotReady as e:
        data = {"ready": False, "message": str(e)}
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
            result = await run_decision_agent(get_decider(), history, user_id=uid, active_application=active)
        else:
            result = await run_agent(get_llm_provider(), history, user_id=uid, active_application=active)
    except LLMError as e:
        raise HTTPException(_STATUS.get(e.code, 502), detail={"code": e.code, "message": e.message})
    return {"reply": result.reply, "sources": [s.model_dump() for s in result.sources], "tools_used": result.tools_used,
            "intent": result.intent, "follow_ups": result.follow_ups, "demo": result.demo,
            "active_application_id": active.application_id if active else None}


# ----------------------------------------------------------------- data for non-chat pages (scraped CHRIST content)
def _light(p):
    return {"id": p.id, "name": p.name, "level": p.level, "campus": p.campus, "application_open": p.application_open,
            "application_close": p.application_close, "duration": p.quick_facts.get("duration"), "url": p.url}


@router.get("/programmes")
def programmes(q: str | None = None, level: str | None = None, campus: str | None = None, offset: int = 0, limit: int = 60):
    c = _corpus()
    rows = c.search_programmes(q, level, campus, limit=10_000)
    limit = max(1, min(limit, 500))
    return {"total": len(rows), "offset": offset, "programmes": [_light(p) for p in rows[offset:offset + limit]],
            "campuses": sorted({p.campus for p in c.programmes if p.campus}), "source": "https://christuniversity.in/our-programmes/All/1"}


@router.get("/programmes/{programme_id}")
def programme(programme_id: str):
    p = _corpus().by_id.get(programme_id)
    if not p:
        raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
    return {"programme": {**p.model_dump(exclude={"tabs"}), "tabs": [{"id": t.id, "title": t.title, "text": t.text} for t in p.tabs]}}


@router.get("/dates")
def dates():
    from collections import Counter
    c = _corpus()
    w = Counter((p.level, p.campus, p.application_open, p.application_close) for p in c.programmes if p.application_close or p.application_open)
    return {"windows": [{"level": lv, "campus": cp, "open": o, "close": cl, "programmes": n} for (lv, cp, o, cl), n in sorted(w.items(), key=lambda x: str(x[0]))],
            "source": "https://christuniversity.in/our-programmes/All/1"}


TOPICS = {
    "scholarships": ({"scholarships"}, "scholarship financial assistance freeship"),
    "documents": ({"admissions"}, "documents required admission application certificates"),
    "campuses": ({"campus"}, "campus facilities library sports transport"),
    "hostel": ({"hostel"}, "hostel accommodation dining"),
    "placements": ({"placements"}, "placement recruiters career"),
    "international": ({"international"}, "international students nri foreign admission"),
    "contact": ({"contact", "admissions"}, "admissions office contact phone email helpline"),
    "about": ({"about"}, "CHRIST University accreditation ranking history"),
    "apply": ({"admissions"}, "how to apply application process online apply last date"),
}


@router.get("/topic/{name}")
def topic(name: str):
    if name not in TOPICS:
        raise HTTPException(404, detail={"code": "not_found", "message": "Unknown topic."})
    cats, query = TOPICS[name]
    hits = _corpus().search(query, k=12, categories=cats) or _corpus().search(query, k=12)
    seen, seen_text, out = set(), set(), []
    for h in hits:
        excerpt = prose(h.chunk.text[:900])[:700]
        key = " ".join(excerpt.split())[:160]
        if h.chunk.url in seen or key in seen_text or not excerpt:   # one card per page, and no identical copies from sibling sites
            continue
        seen.add(h.chunk.url)
        seen_text.add(key)
        out.append({"title": h.chunk.title, "heading": h.chunk.heading, "url": h.chunk.url, "excerpt": excerpt, "external": h.chunk.kind == "external"})
    extra = {}
    if name == "apply":  # the public application portal link most programmes share
        from collections import Counter
        top = Counter(p.apply_url for p in _corpus().programmes if p.apply_url).most_common(1)
        extra["apply_url"] = top[0][0] if top else None
    return {"topic": name, "results": out, **extra}


class EligibilityBody(BaseModel):
    programme_id: str
    percentage: float | None = Field(default=None, ge=0, le=100)


@router.post("/eligibility")
def eligibility(body: EligibilityBody):
    p = _corpus().by_id.get(body.programme_id)
    if not p:
        raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
    return {"programme": {"id": p.id, "name": p.name, "url": p.url}, "eligibility": p.eligibility, "result": hint(p.eligibility, body.percentage)}
