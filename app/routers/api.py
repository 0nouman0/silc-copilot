from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..agent.decision_engine import run_decision_agent
from ..agent.orchestrator import run_agent
from ..agent.dialog import request_key, respond as dialog_respond
from ..agent.prerouter import prerouter
from ..agent.router import route
from ..agent.skills import SKILLS
from ..catalog.record import programme_record
from ..catalog.refresh import current_catalog
from ..catalogue import get_catalogue
from ..eligibility import Profile, check_eligibility
from ..config import BRAND_PRODUCT, BRAND_UNIVERSITY, agent_mode, llm_config
from ..eligibility_hint import hint
from ..llm import LLMError, get_composer, get_decider, get_llm_provider
from .. import live
from ..sources import SourceUnavailable, get_source
from ..sources.text import tidy

router = APIRouter(prefix="/api")
log = logging.getLogger("chat")

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


# ----------------------------------------------------------------- meta
@router.get("/meta")
def meta():
    cfg = llm_config()
    try:
        data = {"ready": True, **get_catalogue().stats()}
        cat = current_catalog()
        if cat is not None:
            data["catalog"] = {"built_at": cat.build.built_at.isoformat(timespec="seconds"), "counts": dict(cat.build.counts), "warnings": len(cat.build.warnings)}
    except SourceUnavailable:
        data = {"ready": False, "message": "The CHRIST programme database cannot be reached."}
    return {"product": BRAND_PRODUCT, "university": BRAND_UNIVERSITY, "data": data,
            "llm": {"provider": cfg.provider, "model": cfg.model, "configured": bool(cfg.api_key), "agent_mode": agent_mode()}}


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
    last_agent: str | None = Field(default=None, pattern="^(info|recommend)$")  # which agent answered last: it keeps the conversation while the user answers it
    last_skill: str | None = Field(default=None, max_length=30)               # the skill that answered last (sticky while the applicant answers it)
    profile: Profile | None = None                                             # what the applicant told us; the frontend resends it every turn
    state: dict | None = None                                                  # conversation state returned by the previous turn (this chat only; validated, never trusted)
    client_msg_id: str | None = Field(default=None, max_length=64, pattern="^[A-Za-z0-9_.-]+$")   # one id per submitted message: a double submit is answered once
    conversation_id: str | None = Field(default=None, max_length=64, pattern="^[A-Za-z0-9_.-]+$")   # one id per chat: keeps two chats' submissions apart


_STATUS = {"not_configured": 503, "rate_limited": 429, "timeout": 504, "unavailable": 502, "tools_unsupported": 502, "bad_response": 502}


_VOICE = {"misses": 0, "down_until": 0.0}                   # circuit breaker for the optional AI wording step
_DONE: dict[str, tuple[float, dict]] = {}          # request key -> (time, response): answers a repeated submission without a second model call
_INFLIGHT: dict[str, "asyncio.Future"] = {}
DONE_TTL, DONE_MAX = 120.0, 500


def _remember(key: str, payload: dict) -> None:
    now = time.monotonic()
    for k in [k for k, (t, _) in _DONE.items() if now - t > DONE_TTL]:
        _DONE.pop(k, None)
    if len(_DONE) >= DONE_MAX:
        _DONE.pop(next(iter(_DONE)), None)
    _DONE[key] = (now, payload)


@router.post("/chat")
async def chat(body: ChatBody, request: Request):
    if body.messages[-1].role != "user":
        raise HTTPException(422, detail={"code": "invalid", "message": "The last message must be from the user."})
    history = [{"role": m.role, "content": m.content if m.role == "user" else m.content[:HISTORY_KEEP]} for m in body.messages]
    key = request_key(body.client_msg_id, history, body.conversation_id)
    if key and key in _DONE and time.monotonic() - _DONE[key][0] <= DONE_TTL:
        return _DONE[key][1]                                   # duplicate submission: the same answer, no new work, no rate-limit hit
    if key and key in _INFLIGHT:
        return await asyncio.shield(_INFLIGHT[key])             # the same message is already being answered: wait for it, never run it twice
    _rate_limit(request)
    fut: "asyncio.Future" = asyncio.get_running_loop().create_future()
    if key:
        _INFLIGHT[key] = fut
    try:
        payload = await _chat_once(body, history)
        if key:
            _remember(key, payload)
        fut.set_result(payload)
        return payload
    except BaseException as e:
        if not fut.done():
            fut.set_exception(e)
            fut.exception()                                     # mark retrieved so a lone caller does not log "never retrieved"
        raise
    finally:
        _INFLIGHT.pop(key, None) if key else None


async def _chat_once(body: ChatBody, history: list[dict]) -> dict:
    try:
        result = prerouter(history, body.profile)   # rules first: injection, escalation, missing-profile clarification (no model involved)
        dialog = None
        if result is None:
            try:
                dialog = await asyncio.to_thread(dialog_respond, history, body.state, body.profile)   # state + strict structured search + small talk, in code
                result = dialog.result
            except Exception:  # noqa: BLE001  a bug here must never kill the chat: log it and let the model path answer
                log.exception("dialog manager failed")
                dialog, result = None, None
            if result is not None:
                log.info("dialog intent=%s state=%s", result.intent, result.state)
        if result is not None:
            pass
        elif agent_mode() == "decisions":
            result = await run_decision_agent(get_decider(), history, composer=get_composer(), last_agent=body.last_agent, profile=body.profile)
        else:
            agent = route(history, body.last_agent)   # each agent has its own prompt, tool set and (optionally) model
            result = await run_agent(get_llm_provider(agent), history, agent=agent, profile=body.profile,
                                     last_skill=body.last_skill if body.last_skill in SKILLS else None, context=dialog.note if dialog else "")
    except LLMError as e:
        raise HTTPException(_STATUS.get(e.code, 502), detail={"code": e.code, "message": e.message})
    if result is not None and getattr(result, "composable", False) and "|" not in result.reply and time.monotonic() >= _VOICE["down_until"]:
        try:                                    # an AI voice may reword verified prose; every number and link must already be in the facts, else the original text stays
            composer = get_composer()
            nice = await composer.compose(history[-1]["content"], history, result.reply, result.intent) if composer else None
            if nice:
                result.reply = nice
                _VOICE["misses"] = 0
            elif composer:                      # models failing or rejected: after two misses in a row stop trying for two minutes so replies stay fast
                _VOICE["misses"] += 1
                if _VOICE["misses"] >= 2:
                    _VOICE.update(down_until=time.monotonic() + 120, misses=0)
        except Exception as e:  # noqa: BLE001  wording is optional: never fail the answer
            log.info("composer skipped: %s", type(e).__name__)
    state = result.state or (dialog.state.as_dict() if dialog is not None else body.state)
    return {"reply": result.reply, "sources": [s.model_dump() for s in result.sources], "tools_used": result.tools_used,
            "intent": result.intent, "follow_ups": result.follow_ups, "agent": result.agent, "skill": result.skill, "cards": result.cards, "state": state,
            "response_id": uuid.uuid4().hex, "in_reply_to": body.client_msg_id,
            "understanding": dialog.interpretation.public() if dialog is not None and dialog.interpretation is not None else None}


WELCOME = "Hello! Welcome to CHRIST University. How can I help you? Which course would you like to enrol in at our college?"


@router.get("/welcome")
def welcome():
    """The first message of every conversation: no database or AI call, so it works even if either is down. Three starting points, not a menu:
    the applicant can always just type."""
    return {"reply": WELCOME, "follow_ups": ["How many programmes are there?", "Tell me about B.Tech", "How do I apply?"], "agent": "recommend"}


# ----------------------------------------------------------------- data for non-chat pages (CHRIST programme database)
DB_SOURCE = "CHRIST programme database"


LIVE_NOTE = "Fees, application dates and seats are live admissions data and are not shown here. Please contact the admissions office."


def _light(p):
    return {"id": p.id, "name": p.name, "level": p.level, "campus": p.campus, "department": p.department, "duration_years": p.duration_years}


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


def _record(programme_id: int):
    cat = current_catalog()
    p = cat.program(programme_id) if cat else None
    return programme_record(cat, p) if p else None


@router.get("/programmes/{programme_id}")
def programme(programme_id: int):
    p = _catalogue().by_id.get(programme_id)
    src = _db()
    try:
        rec = src.get_course(programme_id) if p else None
        if not p or not rec:
            raise HTTPException(404, detail={"code": "not_found", "message": "Programme not found."})
        eligibility = _sections_text(src.get_sections(programme_id, "eligibility")[:3])
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
    return {"programme": {**_light(p), "code": rec.get("code"), "term_type": rec.get("term_type"), "level_label": p.level_label, "summary": rec.get("summary"), "overview": rec.get("overview"),
                          "record_last_modified": rec.get("record_last_modified"), "eligibility": eligibility, "fees": None, "fees_note": LIVE_NOTE, "tabs": tabs,
                          "record": _record(programme_id)},
            "source": DB_SOURCE}


@router.get("/dates")
def dates():
    """Application dates are ERP-only live data. The website's recorded dates are all 2015-2020 and are never served."""
    _catalogue()
    return {"windows": [], "source": DB_SOURCE, "note": "Application dates are live admissions data and are not available here yet. Please contact the admissions office.",
            "contacts": live.admissions_contact().get("campuses", [])}


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
        rows = _db().list_campus_details()
    except SourceUnavailable:
        raise _unavailable()
    return {"campuses": rows, "source": DB_SOURCE}


class EligibilityBody(BaseModel):
    programme_id: int
    percentage: float | None = Field(default=None, ge=0, le=100)
    profile: Profile | None = None
    audience: str | None = Field(default=None, pattern="^(indian|nri|international)$")


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
    out = {"programme": {"id": p.id, "name": p.name, "campus": p.campus}, "eligibility": _sections_text(rows), "result": hint(raw, body.percentage), "source": DB_SOURCE}
    cat = current_catalog()
    prog = cat.program(body.programme_id) if cat else None
    if prog is not None:      # computed verdict from admissions-reviewed rules (cannot_decide until a rule is reviewed)
        prof = (body.profile or Profile()).model_copy(update={"percent": body.profile.percent if body.profile and body.profile.percent is not None else body.percentage})
        out["verdict"] = check_eligibility(prof, prog, body.audience).as_dict()
    return out
