"""HTTP endpoints. This layer only translates between HTTP and Storage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind
from spider.server.auth import make_auth_dependency
from spider.server.storage import Storage

TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_CHUNK = 1024 * 1024


def _chunks(handle: BinaryIO, size: int = _CHUNK, limit: int = 0) -> Iterator[bytes]:
    """Hand the storage layer one chunk at a time so nothing is ever fully
    buffered in memory.

    A positive `limit` is enforced on the bytes actually read, because a
    chunked upload carries no Content-Length to check up front.
    """
    total = 0
    while True:
        chunk = handle.read(size)
        if not chunk:
            return
        total += len(chunk)
        if limit and total > limit:
            raise SpiderError(
                ErrorCode.bad_request, f"Upload exceeds the server limit of {limit} bytes."
            )
        yield chunk


def build_router(storage: Storage, token: str, max_item_mb: int = 0) -> APIRouter:
    require_auth = make_auth_dependency(token)
    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    # Declared with `def`, not `async def`: FastAPI then runs it in a worker
    # thread, where the synchronous file and sqlite3 calls below are safe.
    @router.post("/items", status_code=201, response_model=Item)
    def create_item(
        request: Request,
        content: Annotated[UploadFile, File()],
        kind: Annotated[ItemKind, Form()] = ItemKind.file,
        name: Annotated[str | None, Form()] = None,
        sha256: Annotated[str | None, Form()] = None,
        device: Annotated[str, Form()] = "unknown",
    ) -> Item:
        limit = max_item_mb * 1024 * 1024 if max_item_mb > 0 else 0
        if limit:
            declared = request.headers.get("content-length", "")
            # Cheap early refusal; _chunks still enforces the limit on real bytes.
            if declared.isdigit() and int(declared) > limit:
                raise SpiderError(
                    ErrorCode.bad_request,
                    f"Upload exceeds the server limit of {max_item_mb} MB.",
                )

        if kind is ItemKind.text:
            content_type = TEXT_CONTENT_TYPE
        else:
            content_type = content.content_type or "application/octet-stream"

        return storage.save(
            stream=_chunks(content.file, limit=limit),
            kind=kind,
            name=name or content.filename or "untitled",
            content_type=content_type,
            source_device=device,
            expected_sha256=sha256,
        )

    @router.get("/items")
    def list_items() -> dict:
        return {"items": [], "next_before": None}

    return router
