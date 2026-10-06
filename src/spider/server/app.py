"""Application factory, error translation, and security headers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from spider.core.config import load_server_config
from spider.core.errors import ErrorCode, SpiderError
from spider.server.storage import Storage

STATIC_DIR = Path(__file__).parent / "static"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


def create_app(storage: Storage, token: str, max_item_mb: int = 0) -> FastAPI:
    # No docs endpoints: they are one more surface and nobody reads them here.
    app = FastAPI(title="spider", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.storage = storage
    app.state.token = token

    @app.exception_handler(SpiderError)
    async def handle_spider_error(_: Request, exc: SpiderError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep FastAPI's default 422 body out of the API: every client parses
        # one error shape, and a malformed request is a bad_request.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:]) or 'request'}: {error['msg']}"
            for error in exc.errors()
        )
        error = SpiderError(ErrorCode.bad_request, f"Invalid request. {problems}")
        return JSONResponse(status_code=error.http_status, content=error.to_payload())

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Routes are registered before the static mount so that /api never falls
    # through to the file server.
    from spider.server.routes import build_router

    for sub_router in build_router(storage, token, max_item_mb):
        app.include_router(sub_router)

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    config = load_server_config(env)
    storage = Storage(config.data_dir, min_free_gb=config.min_free_gb)
    storage.init()
    storage.gc()
    storage.apply_retention(config.retention_days)
    return create_app(storage, config.token, config.max_item_mb)
