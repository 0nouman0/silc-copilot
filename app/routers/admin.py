"""Admin endpoints. Disabled unless ADMIN_TOKEN is set; every call is logged with who triggered it.

POST /admin/catalog/refresh   rebuild the in-memory catalog beside the old one and swap it if it passes the checks.
    Skips when no source table checksum changed unless {"force": true}. Must be called on EVERY app instance (each holds its own copy).
GET  /admin/catalog           build info: when, row counts, warnings, per-programme data-quality gaps (the gap report for the web team).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from ..catalog.refresh import current_catalog, refresh
from ..sources.base import SourceUnavailable

log = logging.getLogger("admin")
router = APIRouter(prefix="/admin")


def _auth(request: Request, token: str | None) -> str:
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(404, detail={"code": "not_found", "message": "Not found."})
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        log.warning("admin auth failed from %s", request.client.host if request.client else "?")
        raise HTTPException(403, detail={"code": "forbidden", "message": "Forbidden."})
    return request.client.host if request.client else "unknown"


class RefreshBody(BaseModel):
    force: bool = False


@router.post("/catalog/refresh")
async def catalog_refresh(request: Request, body: RefreshBody | None = None, x_admin_token: str | None = Header(default=None)):
    who = _auth(request, x_admin_token)
    force = bool(body and body.force)
    log.info("catalog refresh requested by %s (force=%s)", who, force)
    try:
        report = await asyncio.to_thread(refresh, force, who)
    except SourceUnavailable as e:
        raise HTTPException(503, detail={"code": "data_unavailable", "message": str(e)})
    return report.as_dict()


@router.get("/catalog")
def catalog_info(request: Request, x_admin_token: str | None = Header(default=None)):
    _auth(request, x_admin_token)
    cat = current_catalog()
    if cat is None:
        raise HTTPException(503, detail={"code": "data_unavailable", "message": "The catalog has not been built."})
    gaps = [{"programme_id": p.id, "name": p.name, "missing": [k.value for k in p.quality.missing], "expired_sections": p.quality.expired_sections,
             "no_erp_id": p.quality.no_erp_id, "campus_inferred": p.quality.campus_inferred, "no_curriculum": not p.curriculum}
            for p in cat.programs.values() if p.quality.missing or p.quality.no_erp_id or p.quality.campus_inferred or not p.curriculum]
    return {"built_at": cat.build.built_at.isoformat(), "counts": dict(cat.build.counts), "warnings": list(cat.build.warnings), "gaps": gaps}
