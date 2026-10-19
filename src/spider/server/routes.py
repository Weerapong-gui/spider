"""HTTP endpoints. This layer only translates between HTTP and Storage."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import (
    BROWSER_DEVICE,
    CLIENT_HEADER,
    DEVICE_HEADER,
    ClientKind,
    Device,
    Item,
    ItemHistory,
    ItemKind,
    ItemPage,
    decode_device,
)
from spider.server.auth import SESSION_COOKIE, make_auth_dependency, token_matches
from spider.server.storage import Storage

TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_CHUNK = 1024 * 1024
_INLINE_EXACT = frozenset({"application/pdf", "text/plain"})


class SessionRequest(BaseModel):
    token: str


class DownloadReport(BaseModel):
    destination: str


@dataclass(frozen=True)
class Caller:
    """Who is making this request. `device` is None for browsers and old clients."""

    device: str | None
    client: ClientKind


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


def build_router(storage: Storage, token: str, max_item_mb: int = 0) -> list[APIRouter]:
    require_auth = make_auth_dependency(token)

    def caller(request: Request) -> None:
        """Work out who is calling and note the device as seen.

        Listed after `require_auth` on the router, which sets `auth_method`; a
        browser is a request that authenticated with the session cookie.
        """
        if request.state.auth_method == "cookie":
            who = Caller(None, ClientKind.browser)
        else:
            raw = request.headers.get(DEVICE_HEADER)
            name = decode_device(raw).strip() if raw else ""
            try:
                client = ClientKind(request.headers.get(CLIENT_HEADER, ""))
            except ValueError:
                client = ClientKind.cli
            # A bearer caller cannot claim to be a browser.
            if client is ClientKind.browser:
                client = ClientKind.cli
            who = Caller(name or None, client)
            if who.device:
                storage.touch_device(who.device, who.client)
        request.state.caller = who

    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth), Depends(caller)])

    def need_device(request: Request) -> str:
        device = request.state.caller.device
        if not device:
            raise SpiderError(ErrorCode.bad_request, f"The {DEVICE_HEADER} header is required.")
        return device

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
        to_device: Annotated[str | None, Form()] = None,
    ) -> Item:
        if to_device:
            known = storage.device_names()
            if to_device not in known:
                raise SpiderError(
                    ErrorCode.bad_request,
                    f"Unknown device {to_device!r}. Known devices: {', '.join(known) or 'none'}.",
                )
            if to_device == request.state.caller.device:
                raise SpiderError(ErrorCode.bad_request, "You cannot send to your own device.")
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
            to_device=to_device or None,
        )

    @router.get("/items", response_model=ItemPage)
    def list_items(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        before: Annotated[str | None, Query()] = None,
        q: Annotated[str | None, Query()] = None,
        kind: Annotated[ItemKind | None, Query()] = None,
    ) -> ItemPage:
        return storage.list_items(limit=limit, before=before, q=q, kind=kind)

    # Literal paths: declared before the parameterised /devices/{name} so
    # "heartbeat" is never taken for a device name.
    @router.post("/devices/heartbeat", status_code=204)
    def beat(request: Request) -> Response:
        storage.heartbeat(need_device(request))
        return Response(status_code=204)

    @router.delete("/devices/heartbeat", status_code=204)
    def stop_beat(request: Request) -> Response:
        storage.clear_heartbeat(need_device(request))
        return Response(status_code=204)

    @router.get("/devices", response_model=list[Device])
    def list_devices() -> list[Device]:
        return storage.list_devices()

    @router.delete("/devices/{name}", status_code=204)
    def remove_device(name: str) -> Response:
        storage.remove_device(name)
        return Response(status_code=204)

    @router.get("/inbox", response_model=list[Item])
    def inbox(request: Request) -> list[Item]:
        return storage.inbox(need_device(request))

    @router.post("/inbox/{ref}/ack", status_code=204)
    def ack(ref: str, request: Request) -> Response:
        device = need_device(request)
        storage.ack(storage.resolve(ref).id, device)
        return Response(status_code=204)

    # Registered before /items/{ref} only by convention; the paths differ, but
    # keeping literal routes first means a future /items/verify cannot be shadowed.
    @router.get("/verify")
    def verify() -> dict:
        return {"missing": [item.model_dump(mode="json") for item in storage.verify()]}

    @router.get("/items/{ref}", response_model=Item)
    def get_item(ref: str) -> Item:
        return storage.resolve(ref)

    @router.get("/items/{ref}/history", response_model=ItemHistory)
    def get_history(ref: str) -> ItemHistory:
        return storage.history(storage.resolve(ref).id)

    @router.post("/items/{ref}/downloads", status_code=204)
    def report_download(ref: str, body: DownloadReport, request: Request) -> Response:
        device = need_device(request)
        item = storage.resolve(ref)
        storage.record_download(item.id, device, request.state.caller.client, body.destination)
        return Response(status_code=204)

    @router.get("/items/{ref}/thumbnail")
    def get_thumbnail(ref: str) -> FileResponse:
        path = storage.thumbnail(storage.resolve(ref).id)
        if path is None:
            raise SpiderError(ErrorCode.not_found, "No thumbnail for this item.")
        return FileResponse(path, media_type="image/png")

    @router.get("/items/{ref}/content")
    def get_content(
        ref: str,
        request: Request,
        disposition: Annotated[str, Query()] = "attachment",
        purpose: Annotated[str | None, Query()] = None,
    ) -> StreamingResponse:
        item = storage.resolve(ref)
        # Opened first, so a missing blob never leaves a download in the history.
        handle = storage.open_blob(item.id)
        if request.state.caller.client is ClientKind.browser and disposition != "inline":
            # Recorded when the send starts: only the browser knows if it finished.
            ip = request.client.host if request.client else "unknown"
            destination = f"clipboard ({ip})" if purpose == "clipboard" else ip
            try:
                storage.record_download(item.id, BROWSER_DEVICE, ClientKind.browser, destination)
            except BaseException:
                handle.close()
                raise
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

    # Not under the authenticated router: exchanging a token for a cookie is how
    # a browser gets credentials in the first place.
    session_router = APIRouter(prefix="/api")

    @session_router.post("/session", status_code=204)
    def create_session(body: SessionRequest) -> Response:
        if not token_matches(token, body.token):
            raise SpiderError(ErrorCode.unauthorized, "That token is not correct.")
        response = Response(status_code=204)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=60 * 60 * 24 * 365,
        )
        return response

    @session_router.delete("/session", status_code=204)
    def destroy_session() -> Response:
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    return [router, session_router]
