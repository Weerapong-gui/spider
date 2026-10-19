"""What each button does, with no Qt in sight.

Every function takes a `SpiderClient` (or plain values) and returns plain
values, so they can be tested against the real app without a display. The
window runs them on a worker thread.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from spider.cli.client import SpiderClient
from spider.cli.main import humanize_size, safe_filename
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Device, Item, ItemHistory, ItemKind

TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
LIST_LIMIT = 200
# Every request a poll makes gives up after this long. The window runs one poll at
# a time, so a request that never answers (a laptop waking from sleep, Tailscale
# dropping mid-request) would otherwise stop heartbeats and notices for good.
POLL_TIMEOUT = 15.0
ICON_DIR = Path(__file__).parent / "icons"

# Material Icon Theme (MIT) stems in ICON_DIR. Extension wins because the content
# type is often a generic guess; the family is the fallback for extensionless names.
ICON_BY_EXTENSION = {
    **dict.fromkeys(("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "tiff"), "image"),
    "pdf": "pdf",
    "txt": "text",
    "log": "text",
    "md": "markdown",
    "py": "python",
    "js": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "html": "html",
    "htm": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    **dict.fromkeys(("sh", "bash", "zsh"), "shell"),
    **dict.fromkeys(("zip", "tar", "gz", "tgz", "7z", "bz2", "xz", "rar"), "zip"),
    **dict.fromkeys(("mp3", "wav", "flac", "m4a", "ogg", "aac"), "audio"),
    **dict.fromkeys(("mp4", "mov", "mkv", "avi", "webm"), "video"),
    **dict.fromkeys(("doc", "docx"), "word"),
    **dict.fromkeys(("xls", "xlsx"), "excel"),
    **dict.fromkeys(("ppt", "pptx"), "powerpoint"),
    "csv": "csv",
}
ICON_BY_FAMILY = {"image": "image", "audio": "audio", "video": "video", "text": "text"}


def clipboard_label(text: str) -> str:
    """Name a clipboard item after its first line so the list is readable."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return " ".join(first.split())[:40] or "clipboard"


def send_text(client: SpiderClient, text: str, to_device: str | None = None) -> Item:
    if not text.strip():
        raise SpiderError(ErrorCode.bad_request, "The clipboard is empty. Copy something first.")
    return client.push_bytes(
        text.encode("utf-8"),
        kind=ItemKind.text,
        name=clipboard_label(text),
        content_type=TEXT_CONTENT_TYPE,
        to_device=to_device,
    )


def send_files(client: SpiderClient, paths: list[Path], to_device: str | None = None) -> list[Item]:
    sent: list[Item] = []
    for path in paths:
        if not path.is_file():
            raise SpiderError(
                ErrorCode.bad_request, f"{path} is not a file (folders are not sent)."
            )
        sent.append(client.push_file(path, to_device=to_device))
    return sent


def report_quietly(client: SpiderClient, item_id: str, destination: str) -> str | None:
    """Record a finished download without letting a failure undo it.

    The bytes are already where the user wanted them; losing a history row must not
    turn that success into an error. Returns the message to show, or None. (The CLI
    has its own that prints to stderr; a GUI worker must hand the text back.)
    """
    try:
        client.report_download(item_id, destination)
    except SpiderError as exc:
        return f"Could not record this download: {exc.message}"
    return None


def fetch_text(client: SpiderClient, ref: str, report: bool = True) -> tuple[Item, str]:
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
        text = buffer.getvalue().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpiderError(
            ErrorCode.bad_request,
            f"{item.name} is not valid UTF-8 text. Use Save as… instead.",
        ) from exc
    if report:
        report_quietly(client, item.id, "clipboard")
    return item, text


def save_item(client: SpiderClient, item_id: str, target: Path) -> tuple[Item, str | None]:
    """Download to a sibling `.part` file, so a failed or corrupted download
    never replaces a good local file. Returns the item and a history warning, if any."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".part")
    try:
        with staging.open("wb") as handle:
            item = client.download(item_id, handle)
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return item, report_quietly(client, item.id, str(target.resolve()))


def unique_path(directory: Path, filename: str) -> Path:
    """`report.pdf` -> `report (1).pdf`: a file in Downloads is never overwritten."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    # Keep every suffix together (archive.tar.gz) but treat a leading dot as part
    # of the name (.bashrc), as Path.suffixes would not.
    body = filename.lstrip(".")
    lead = filename[: len(filename) - len(body)]
    stem, dot, rest = body.partition(".")
    stem, extension = lead + stem, dot + rest
    number = 1
    while True:
        candidate = directory / f"{stem} ({number}){extension}"
        if not candidate.exists():
            return candidate
        number += 1


def downloads_dir() -> Path:
    return Path.home() / "Downloads"


@dataclass
class Received:
    item: Item
    path: Path | None
    text: str | None
    warning: str | None


def receive(client: SpiderClient, item_id: str, downloads: Path) -> Received:
    """What clicking a notification does: files land in Downloads, text is returned
    for the clipboard. A deleted item raises not_found from the server."""
    item = client.resolve(item_id)
    if item.kind is ItemKind.text:
        _, text = fetch_text(client, item.id, report=False)
        return Received(item, None, text, report_quietly(client, item.id, "clipboard"))
    downloads.mkdir(parents=True, exist_ok=True)
    path = unique_path(downloads, safe_filename(item.name))
    saved, warning = save_item(client, item.id, path)
    return Received(saved, path, None, warning)


@dataclass
class Notice:
    title: str
    message: str
    item_ids: list[str]
    # Said outright so the window never has to read them back out of the message.
    is_text: bool
    names: list[str]


def group_notices(items: list[Item]) -> list[Notice]:
    """One notice per sender's files, one per text: the clipboard holds a single
    value, so grouping texts would silently drop all but one."""
    files: dict[str, list[Item]] = {}
    slots: list[Item | str] = []  # first-seen order: a text item, or a sender name
    for item in items:
        if item.kind is ItemKind.text:
            slots.append(item)
        else:
            if item.source_device not in files:
                slots.append(item.source_device)
            files.setdefault(item.source_device, []).append(item)
    notices: list[Notice] = []
    for slot in slots:
        if isinstance(slot, Item):
            title = f"spider · from {slot.source_device}"
            message = f"{slot.name} — click to copy to the clipboard"
            notices.append(Notice(title, message, [slot.id], True, [slot.name]))
            continue
        group = files[slot]
        if len(group) == 1:
            only = group[0]
            message = f"{only.name} ({humanize_size(only.size)}) — click to save to Downloads"
        else:
            message = f"{len(group)} files from {slot} — click to receive all"
        ids, names = [i.id for i in group], [i.name for i in group]
        notices.append(Notice(f"spider · from {slot}", message, ids, False, names))
    return notices


@dataclass
class Poll:
    devices: list[Device] | None
    notices: list[Notice]


def poll(client: SpiderClient) -> Poll:
    """One heartbeat round. `devices` is None against an old server, which answers
    the new routes with a plain 404 (mapped to `unreachable`, not `not_found`).

    The heartbeat doubles as the probe for an old server, and goes first so this
    device is already online in the list it fetches."""
    if not client.heartbeat(timeout=POLL_TIMEOUT):
        return Poll(None, [])
    devices = client.devices(timeout=POLL_TIMEOUT)
    acked: list[Item] = []
    for item in client.inbox(timeout=POLL_TIMEOUT):
        # Stop at the first failure but keep what was acked: those are never offered
        # again, so they must reach the window. The rest come back next poll.
        try:
            client.ack(item.id, timeout=POLL_TIMEOUT)
        except SpiderError:
            break
        acked.append(item)
    return Poll(devices, group_notices(acked))


def load_history(client: SpiderClient, ref: str) -> ItemHistory | None:
    """None when the server predates history.

    An old server's plain 404 maps to `unreachable`, which a real outage also is, so
    only on failure do we spend a second request asking whether the routes exist.
    """
    try:
        return client.history(ref)
    except SpiderError:
        if not client.supports_devices():
            return None
        raise


def suggested_filename(item: Item) -> str:
    return safe_filename(item.name)


def list_items(client: SpiderClient, query: str | None) -> list[Item]:
    return client.list_items(limit=LIST_LIMIT, q=query or None).items


def delete_item(client: SpiderClient, item_id: str) -> None:
    client.delete(item_id)


def icon_name(name: str, content_type: str, kind: ItemKind) -> str:
    """Pick the icon stem for a list row: text items, extension, then content-type family.

    Text items are named after their first line, so a snippet called "see main.py"
    must not look like a Python file.
    """
    if kind is ItemKind.text:
        return "text"
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in ICON_BY_EXTENSION:
        return ICON_BY_EXTENSION[extension]
    family = content_type.split("/", 1)[0].strip().lower()
    if family in ICON_BY_FAMILY:
        return ICON_BY_FAMILY[family]
    return "file"
