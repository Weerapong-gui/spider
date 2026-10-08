"""The only module in the CLI that speaks HTTP.

Every server error arrives as the shared `{"error": {"code", "message"}}`
shape and leaves this module as a `SpiderError`, so the command layer above
never has to think about status codes.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import BinaryIO

import httpx

from spider.core.config import ClientConfig
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind, ItemPage

_DOWNLOAD_CHUNK = 1024 * 1024
_HASH_CHUNK = 1024 * 1024


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class SpiderClient:
    def __init__(
        self,
        config: ClientConfig,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.config = config
        self._http = httpx.Client(
            base_url=config.server,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=httpx.Timeout(timeout, read=None, write=None),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SpiderClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _raise_for_error(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()["error"]
            code = ErrorCode(payload["code"])
            message = payload["message"]
        except (ValueError, KeyError, TypeError):
            code = ErrorCode.unreachable
            message = f"Server returned HTTP {response.status_code}."
        raise SpiderError(code, message)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SpiderError(
                ErrorCode.unreachable,
                f"Cannot reach the server at {self.config.server}. Is it running, "
                "and is Tailscale connected?",
            ) from exc
        except httpx.HTTPError as exc:
            raise SpiderError(ErrorCode.unreachable, f"Network error: {exc}") from exc
        self._raise_for_error(response)
        return response

    def push_bytes(
        self,
        payload: bytes,
        *,
        kind: ItemKind,
        name: str,
        content_type: str | None = None,
    ) -> Item:
        guessed = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = self._request(
            "POST",
            "/api/items",
            files={"content": (name, payload, guessed)},
            data={
                "kind": kind.value,
                "name": name,
                "device": self.config.device,
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        return Item.model_validate(response.json())

    def push_file(self, path: Path, *, name: str | None = None) -> Item:
        """Hash the file, then stream it.

        Reading twice costs a second pass over the disk, and buys an end-to-end
        check that the bytes the server stored are the bytes this machine had.
        """
        display_name = name or path.name
        checksum = sha256_of_file(path)
        content_type = mimetypes.guess_type(display_name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            response = self._request(
                "POST",
                "/api/items",
                files={"content": (display_name, handle, content_type)},
                data={
                    "kind": ItemKind.file.value,
                    "name": display_name,
                    "device": self.config.device,
                    "sha256": checksum,
                },
            )
        return Item.model_validate(response.json())

    def list_items(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        q: str | None = None,
        kind: ItemKind | None = None,
    ) -> ItemPage:
        params: dict[str, object] = {"limit": limit}
        if before:
            params["before"] = before
        if q:
            params["q"] = q
        if kind is not None:
            params["kind"] = kind.value
        return ItemPage.model_validate(self._request("GET", "/api/items", params=params).json())

    def resolve(self, ref: str) -> Item:
        return Item.model_validate(self._request("GET", f"/api/items/{ref}").json())

    def download(self, ref: str, destination: BinaryIO) -> Item:
        item = self.resolve(ref)
        digest = hashlib.sha256()
        with self._http.stream(
            "GET", f"/api/items/{item.id}/content", headers={"Accept": "*/*"}
        ) as response:
            self._raise_for_error(response)
            for chunk in response.iter_bytes(_DOWNLOAD_CHUNK):
                destination.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != item.sha256:
            raise SpiderError(
                ErrorCode.checksum_mismatch,
                f"Downloaded bytes do not match. Expected {item.sha256}, "
                f"got {digest.hexdigest()}. The stored file may be damaged.",
            )
        return item

    def delete(self, ref: str) -> None:
        self._request("DELETE", f"/api/items/{ref}")

    def verify(self) -> list[Item]:
        payload = self._request("GET", "/api/verify").json()
        return [Item.model_validate(entry) for entry in payload["missing"]]
