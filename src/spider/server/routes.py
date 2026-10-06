"""HTTP endpoints. Filled in by the next tasks."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from spider.server.auth import make_auth_dependency
from spider.server.storage import Storage


def build_router(storage: Storage, token: str) -> APIRouter:
    require_auth = make_auth_dependency(token)
    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    @router.get("/items")
    def list_items() -> dict:
        return {"items": [], "next_before": None}

    return router
