"""The FastAPI application.

Run locally or on a host with:  uvicorn api.main:app

The patient web page (added in a later batch) lives in ``static/patient`` and is
served from the same address, so no cross-origin access is needed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from api.errors import error_response, register_error_handlers
from api.routers import auth, consent, patient
from api.schemas import HealthOut
from config.settings import DEMO_BANNER_TEXT, NO_STORE_CACHE_CONTROL, SECURITY_HEADERS
from db.connection import close_pool, health_check, warm_up

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16_384
DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "patient"
# The interactive docs page needs scripts a strict policy would block.
_DOCS_PATHS = ("/docs", "/redoc")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    warm_up()  # wake the database early (Neon may be idle); failure here is not fatal
    yield
    close_pool()


def _apply_headers(request: Request, response: Response) -> None:
    """BR-22: security headers everywhere, and no caching of API data."""
    on_docs = request.url.path.startswith(_DOCS_PATHS)
    for name, value in SECURITY_HEADERS.items():
        if on_docs and name == "Content-Security-Policy":
            continue
        response.headers[name] = value
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = NO_STORE_CACHE_CONTROL


def create_app(static_dir: Optional[Path] = DEFAULT_STATIC_DIR) -> FastAPI:
    """Build the application (a function so tests can build fresh copies)."""
    app = FastAPI(
        title="Patient Rescheduling MVP API (DEMO - synthetic data only)",
        description=DEMO_BANNER_TEXT,
        version="0.2.0",
        lifespan=_lifespan,
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def protect(request: Request, call_next) -> Response:
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            response: Response = error_response(413, "payload_too_large")
        else:
            try:
                response = await call_next(request)
            except Exception as exc:  # last resort: still a standard, safe response
                logger.error("Unhandled error: %s", type(exc).__name__)
                response = error_response(500, "internal")
        _apply_headers(request, response)
        return response

    app.include_router(auth.router)
    app.include_router(consent.router)
    app.include_router(patient.router)

    @app.get("/api/v1/health", response_model=HealthOut, tags=["system"])
    def health() -> JSONResponse:
        """Service and database status (for hosting health checks)."""
        status = health_check()
        return JSONResponse(status_code=200 if status["database"] == "ok" else 503, content=status)

    if static_dir is not None and Path(static_dir).is_dir():
        app.mount("/patient", StaticFiles(directory=str(static_dir), html=True), name="patient")

        @app.get("/", include_in_schema=False)
        def root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/patient/")
    else:

        @app.get("/", include_in_schema=False)
        def root_info() -> dict[str, object]:
            return {
                "name": "Patient Rescheduling MVP API",
                "demo": DEMO_BANNER_TEXT,
                "docs": "/docs",
            }

    return app


app = create_app()
