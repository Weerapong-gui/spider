"""Shared data model. Imported by both the server and the CLI."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote, unquote

from pydantic import BaseModel

DEVICE_HEADER = "X-Spider-Device"
CLIENT_HEADER = "X-Spider-Client"
BROWSER_DEVICE = "browser"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_ulid_lock = threading.Lock()
_last_ulid_value = 0


def new_ulid() -> str:
    """Return a 26-character ULID: 48 bits of millisecond time, 80 bits random.

    Lexicographic order matches creation order, so the database never needs a
    separate sequence column to sort by. Two calls landing in the same
    millisecond would otherwise sort at random, so a value that does not exceed
    the last one issued is bumped to one past it.

    The lock makes that read-modify-write atomic. The server declares its
    endpoints with `def`, so FastAPI runs them in a thread pool, and two
    devices uploading at once reach this function in parallel. Without the
    lock, both threads can read the same stale value and issue the same id.
    """
    global _last_ulid_value
    timestamp_ms = int(time.time() * 1000)
    with _ulid_lock:
        value = (timestamp_ms << 80) | int.from_bytes(os.urandom(10), "big")
        if value <= _last_ulid_value:
            value = _last_ulid_value + 1
        _last_ulid_value = value
    return "".join(_CROCKFORD[(value >> (5 * shift)) & 0x1F] for shift in range(25, -1, -1))


def encode_device(name: str) -> str:
    """Percent-encode a device name so it survives latin-1 HTTP headers."""
    return quote(name, safe="")


def decode_device(value: str) -> str:
    return unquote(value)


class ItemKind(StrEnum):
    text = "text"
    file = "file"


class Item(BaseModel):
    """One thing stored on the server. Text and files differ only by `kind`."""

    id: str
    kind: ItemKind
    name: str
    size: int
    sha256: str
    content_type: str
    created_at: datetime
    source_device: str
    preview: str | None = None
    to_device: str | None = None


class ClientKind(StrEnum):
    gui = "gui"
    cli = "cli"
    browser = "browser"


class Device(BaseModel):
    name: str
    client: ClientKind
    last_seen: datetime
    online: bool


class Download(BaseModel):
    device: str
    client: ClientKind
    destination: str
    at: datetime


class ItemHistory(BaseModel):
    item: Item
    downloads: list[Download]


class ItemPage(BaseModel):
    """One page of a cursor-paginated listing."""

    items: list[Item]
    next_before: str | None = None
