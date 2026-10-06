"""HTTP endpoints. This layer only translates between HTTP and Storage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind, ItemPage
from spider.server.auth import make_auth_dependency
from spider.server.storage import Storage

TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_CHUNK = 1024 * 1024
_INLINE_EXACT = frozenset({"application/pdf", "text/plain"})


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


def is_inline_safe(content_type: str) -> bool:
    """Whether a browser may render this type in place.

    Files are served from the same origin as the app, so an inline HTML or SVG
    document would run its scripts with full access to the page. Images and
    PDFs cannot, so they are the whitelist.
    """
    base = content_type.split(";")[0].strip().lower()
    if base == "image/svg+xml":
        return False
    return base.startswith("image/") or base in _INLINE_EXACT


def content_disposition(name: str, inline: bool) -> str:
    kind = "inline" if inline else "attachment"
    # The quoted fallback must not be able to end the string or the header.
    ascii_name = "".join(
        char for char in name if char.isascii() and char.isprintable() and char not in '"\\'
    )
    return f"{kind}; filename=\"{ascii_name or 'download'}\"; filename*=UTF-8''{quote(name)}"


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

    @router.get("/items", response_model=ItemPage)
    def list_items(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        before: Annotated[str | None, Query()] = None,
        q: Annotated[str | None, Query()] = None,
        kind: Annotated[ItemKind | None, Query()] = None,
    ) -> ItemPage:
        return storage.list_items(limit=limit, before=before, q=q, kind=kind)

    # Registered before /items/{ref} only by convention; the paths differ, but
    # keeping literal routes first means a future /items/verify cannot be shadowed.
    @router.get("/verify")
    def verify() -> dict:
        return {"missing": [item.model_dump(mode="json") for item in storage.verify()]}

    @router.get("/items/{ref}", response_model=Item)
    def get_item(ref: str) -> Item:
        return storage.resolve(ref)

    @router.get("/items/{ref}/content")
    def get_content(
        ref: str, disposition: Annotated[str, Query()] = "attachment"
    ) -> StreamingResponse:
        item = storage.resolve(ref)
        handle = storage.open_blob(item.id)
        inline = disposition == "inline" and is_inline_safe(item.content_type)

        def stream() -> Iterator[bytes]:
            try:
                yield from _chunks(handle)
            finally:
                handle.close()

        return StreamingResponse(
            stream(),
            media_type=item.content_type,
            headers={
                "Content-Length": str(item.size),
                "Content-Disposition": content_disposition(item.name, inline),
                "X-Spider-Sha256": item.sha256,
            },
        )

    @router.delete("/items/{ref}", status_code=204)
    def delete_item(ref: str) -> Response:
        item = storage.resolve(ref)
        storage.delete(item.id)
        return Response(status_code=204)

    return router
