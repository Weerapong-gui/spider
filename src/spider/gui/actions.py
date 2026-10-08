"""What each button does, with no Qt in sight.

Every function takes a `SpiderClient` (or plain values) and returns plain
values, so they can be tested against the real app without a display. The
window runs them on a worker thread.
"""

from __future__ import annotations

import io
from pathlib import Path

from spider.cli.client import SpiderClient
from spider.cli.main import safe_filename
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind

TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
LIST_LIMIT = 200


def clipboard_label(text: str) -> str:
    """Name a clipboard item after its first line so the list is readable."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return " ".join(first.split())[:40] or "clipboard"


def send_text(client: SpiderClient, text: str) -> Item:
    if not text.strip():
        raise SpiderError(ErrorCode.bad_request, "The clipboard is empty. Copy something first.")
    return client.push_bytes(
        text.encode("utf-8"),
        kind=ItemKind.text,
        name=clipboard_label(text),
        content_type=TEXT_CONTENT_TYPE,
    )


def send_files(client: SpiderClient, paths: list[Path]) -> list[Item]:
    sent: list[Item] = []
    for path in paths:
        if not path.is_file():
            raise SpiderError(
                ErrorCode.bad_request, f"{path} is not a file (folders are not sent)."
            )
        sent.append(client.push_file(path))
    return sent


def fetch_text(client: SpiderClient, ref: str) -> tuple[Item, str]:
    """Download a text item and decode it, refusing anything that is not text."""
    item = client.resolve(ref)
    if item.kind is not ItemKind.text:
        raise SpiderError(
            ErrorCode.bad_request,
            f"{item.name} is a file, not text. Use Save as… instead.",
        )
    buffer = io.BytesIO()
    client.download(item.id, buffer)
    try:
        return item, buffer.getvalue().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpiderError(
            ErrorCode.bad_request,
            f"{item.name} is not valid UTF-8 text. Use Save as… instead.",
        ) from exc


def save_item(client: SpiderClient, item_id: str, target: Path) -> Item:
    """Download to a sibling `.part` file, so a failed or corrupted download
    never replaces a good local file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".part")
    try:
        with staging.open("wb") as handle:
            item = client.download(item_id, handle)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return item


def suggested_filename(item: Item) -> str:
    return safe_filename(item.name)


def list_items(client: SpiderClient, query: str | None) -> list[Item]:
    return client.list_items(limit=LIST_LIMIT, q=query or None).items


def delete_item(client: SpiderClient, item_id: str) -> None:
    client.delete(item_id)
