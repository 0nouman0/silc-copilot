from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .catalog.refresh import current_catalog, get_reader, maybe_refresh, refresh
from .config import BRAND_PRODUCT
from .routers.admin import router as admin_router
from .routers.api import router as api_router

log = logging.getLogger("app")

STATIC = Path(__file__).resolve().parent.parent / "static"

async def _safety_net(minutes: float):
    """Every few minutes rebuild only if a source table checksum changed (course_related_links and course_info have no modified-date column)."""
    while True:
        await asyncio.sleep(minutes * 60)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(maybe_refresh, "timer")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Build the catalog at startup (in the background: the app answers 503 'database unavailable' until it is ready) and keep it fresh."""
    tasks: list[asyncio.Task] = []
    if get_reader() is not None:
        async def first_build():
            try:
                await asyncio.to_thread(refresh, True, "startup")
            except Exception as e:  # noqa: BLE001  the app must still start; tools report the catalog as unavailable
                log.error("catalog startup build failed: %s", e)
        tasks.append(asyncio.create_task(first_build()))
        minutes = float(os.getenv("CATALOG_CHECK_MINUTES", "5"))
        if minutes > 0:
            tasks.append(asyncio.create_task(_safety_net(minutes)))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title=BRAND_PRODUCT, docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.include_router(api_router)
app.include_router(admin_router)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    # Uniform {error:{code,message}} shape for the frontend.
    d = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": d}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse({"error": {"code": "invalid_request", "message": "Please check your input and try again."}},
                        status_code=422)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/readyz")
def ready():
    """200 once the catalog is built; 503 before. Load balancers use this, not /healthz."""
    cat = current_catalog()
    if cat is None:
        raise HTTPException(503, detail={"code": "not_ready", "message": "The catalog is still building."})
    return {"ok": True, "programmes": len(cat.programs), "built_at": cat.build.built_at.isoformat(timespec="seconds")}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Single-page app: any non-API path serves the shell; client-side router handles the view."""
    if path.startswith("api/"):
        raise HTTPException(404, detail={"code": "not_found", "message": "Not found."})
    return FileResponse(STATIC / "index.html")
