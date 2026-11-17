from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BRAND_PRODUCT
from .routers.api import router as api_router

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title=BRAND_PRODUCT, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(api_router)


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


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Single-page app: any non-API path serves the shell; client-side router handles the view."""
    if path.startswith("api/"):
        raise HTTPException(404, detail={"code": "not_found", "message": "Not found."})
    return FileResponse(STATIC / "index.html")
