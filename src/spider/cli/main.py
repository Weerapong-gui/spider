"""Command line interface.

Everything here is presentation: reading arguments, formatting output, and
turning a `SpiderError` into the right exit code. The work happens in
`SpiderClient`.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from spider.cli.client import SpiderClient
from spider.cli.clipboard import read_clipboard, write_clipboard
from spider.core.config import (
    MIN_TOKEN_LENGTH,
    ClientConfig,
    client_config_path,
    default_device_name,
    has_insecure_permissions,
    load_client_config,
    save_client_config,
)
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Move text and files between your machines.",
)

out = Console()
err = Console(stderr=True)


SHORT_ID_LENGTH = 12


def short_id(item: Item) -> str:
    """The first 12 characters of the ULID.

    The first 10 are a millisecond timestamp, so a 7-character prefix is shared
    by everything created within about 30 seconds and cannot be used to name an
    item. Twelve reaches into the random part.
    """
    return item.id[:SHORT_ID_LENGTH]


def safe_filename(name: str) -> str:
    """Reduce a stored name to a bare file name.

    The server keeps whatever name the uploader sent, so `../../.bashrc` is a
    legal item name. Pulling it must never write outside the target folder.
    """
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return leaf if leaf not in ("", ".", "..") else "download"


def humanize_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def humanize_age(when: datetime) -> str:
    seconds = int((datetime.now(UTC) - when).total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def build_client() -> SpiderClient:
    path = client_config_path()
    if has_insecure_permissions(path):
        err.print(
            f"[yellow]warning[/yellow] {path} is readable by other users. "
            f"Fix it with: chmod 600 {path}"
        )
    return SpiderClient(load_client_config(path))


def fail(exc: SpiderError) -> None:
    err.print(f"[red]error[/red] {exc.message}")
    raise typer.Exit(exc.exit_code)


def render_items(items: list[Item]) -> None:
    """A table on a terminal, tab-separated values anywhere else.

    `spider ls | awk` has to work, so nothing is coloured or aligned when
    stdout is a pipe.
    """
    if not sys.stdout.isatty():
        for item in items:
            print(
                "\t".join(
                    [
                        short_id(item),
                        item.kind.value,
                        item.name,
                        str(item.size),
                        item.source_device,
                        item.created_at.isoformat(),
                    ]
                )
            )
        return

    table = Table(box=None, pad_edge=False)
    for column in ("ID", "KIND", "NAME", "SIZE", "FROM", "WHEN"):
        table.add_column(column)
    for item in items:
        table.add_row(
            short_id(item),
            item.kind.value,
            item.name,
            humanize_size(item.size),
            item.source_device,
            humanize_age(item.created_at),
        )
    out.print(table)


@app.command()
def init(
    server: str = typer.Option(..., prompt="Server URL (for example http://100.95.121.2:8181)"),
    token: str = typer.Option(..., prompt="Token", hide_input=True),
    device: str = typer.Option(default_device_name, prompt="This device's name"),
) -> None:
    """Save the server address and token for this machine."""
    # Strip first: these values are pasted, and a trailing newline would
    # otherwise become part of the token and never match the server's.
    server, token, device = server.strip(), token.strip(), device.strip()
    if len(token) < MIN_TOKEN_LENGTH:
        err.print(
            f"[red]error[/red] The token is {len(token)} characters; "
            f"it must be at least {MIN_TOKEN_LENGTH}."
        )
        raise typer.Exit(2)

    path = save_client_config(
        ClientConfig(server=server.rstrip("/"), token=token, device=device),
        client_config_path(),
    )
    out.print(f"Saved to {path} (permissions 600)")

    from spider.cli.clipboard import clipboard_available, clipboard_hint

    if not clipboard_available():
        err.print(f"[yellow]note[/yellow] {clipboard_hint()}")


@app.command()
def push(
    paths: list[str] = typer.Argument(None, help="Files to send. Use - to read stdin."),  # noqa: B008
    text: str = typer.Option(None, "-t", "--text", help="Send this text instead of a file."),
    name: str = typer.Option(None, "--name", help="Override the stored name."),
) -> None:
    """Send files or text to the server."""
    client = build_client()
    try:
        if text is not None:
            item = client.push_bytes(
                text.encode("utf-8"),
                kind=ItemKind.text,
                name=name or "text",
                content_type="text/plain; charset=utf-8",
            )
            out.print(f"{short_id(item)}  {item.name}  {humanize_size(item.size)}")
            return

        if not paths:
            fail(SpiderError(ErrorCode.bad_request, "Give a file to push, - for stdin, or -t."))

        for raw in paths:
            if raw == "-":
                payload = sys.stdin.buffer.read()
                item = client.push_bytes(
                    payload,
                    kind=ItemKind.text,
                    name=name or "stdin",
                    content_type="text/plain; charset=utf-8",
                )
            else:
                path = Path(raw)
                if not path.is_file():
                    fail(SpiderError(ErrorCode.not_found, f"No such file: {raw}"))
                item = client.push_file(path, name=name)
            out.print(f"{short_id(item)}  {item.name}  {humanize_size(item.size)}")
    except SpiderError as exc:
        fail(exc)


@app.command("ls")
def list_command(
    limit: int = typer.Option(50, "-n", "--limit"),
    q: str = typer.Option(None, "-q", "--search"),
    files: bool = typer.Option(False, "--files", help="Only files."),
    texts: bool = typer.Option(False, "--texts", help="Only text."),
) -> None:
    """List what is on the server, newest first."""
    kind = ItemKind.file if files else ItemKind.text if texts else None
    try:
        page = build_client().list_items(limit=limit, q=q, kind=kind)
    except SpiderError as exc:
        fail(exc)
    render_items(page.items)


@app.command()
def pull(
    ref: str = typer.Argument("latest", help="Item id, id prefix, or latest."),
    output: str = typer.Option(None, "-o", "--output", help="Where to write it."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Download an item to this machine."""
    client = build_client()
    try:
        item = client.resolve(ref)
        target = Path(output) if output else Path.cwd() / safe_filename(item.name)
        if target.exists() and not force:
            fail(
                SpiderError(
                    ErrorCode.bad_request,
                    f"{target} already exists. Pass --force to overwrite, or -o for another path.",
                )
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temporary file so a failed or corrupted download
        # never replaces a good local file.
        staging = target.with_name(target.name + ".part")
        try:
            with staging.open("wb") as handle:
                client.download(item.id, handle)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
    except SpiderError as exc:
        fail(exc)
    out.print(f"{target}  {humanize_size(item.size)}  sha256 verified")


@app.command("cat")
def cat_command(ref: str = typer.Argument("latest")) -> None:
    """Write an item's bytes to stdout. Works on machines with no clipboard."""
    try:
        build_client().download(ref, sys.stdout.buffer)
        sys.stdout.buffer.flush()
    except SpiderError as exc:
        fail(exc)


@app.command("rm")
def remove_command(ref: str = typer.Argument(...)) -> None:
    """Delete an item from the server."""
    client = build_client()
    try:
        item = client.resolve(ref)
        client.delete(item.id)
    except SpiderError as exc:
        fail(exc)
    out.print(f"deleted {short_id(item)}  {item.name}")


@app.command()
def verify() -> None:
    """Check that every item still has its bytes on the server."""
    client = build_client()
    try:
        missing = client.verify()
        total = len(client.list_items(limit=200).items)
        total_label = f"{total}+" if total >= 200 else str(total)
    except SpiderError as exc:
        fail(exc)
    if not missing:
        out.print(f"All {total_label} item(s) have their stored files.")
        return
    err.print(f"[red]{len(missing)} of {total_label} item(s) are missing their stored file:[/red]")
    render_items(missing)
    raise typer.Exit(1)


def _clipboard_label(text: str) -> str:
    """Name the item after its first line so `spider ls` is readable."""
    first = text.strip().splitlines()[0] if text.strip() else "clipboard"
    label = " ".join(first.split())[:40]
    return label or "clipboard"


@app.command("copy")
def copy_command(
    name: str = typer.Option(None, "--name", help="Override the stored name."),
) -> None:
    """Send this machine's clipboard to the server."""
    try:
        text = read_clipboard()
        item = build_client().push_bytes(
            text.encode("utf-8"),
            kind=ItemKind.text,
            name=name or _clipboard_label(text),
            content_type="text/plain; charset=utf-8",
        )
    except SpiderError as exc:
        fail(exc)
    out.print(f"copied {humanize_size(item.size)} as {short_id(item)}  {item.name}")


@app.command("paste")
def paste_command(
    ref: str = typer.Argument("latest", help="Item id, id prefix, or latest."),
) -> None:
    """Put an item from the server onto this machine's clipboard."""
    client = build_client()
    try:
        item = client.resolve(ref)
        if item.kind is not ItemKind.text:
            fail(
                SpiderError(
                    ErrorCode.bad_request,
                    f"{item.name} is a file, not text. Use `spider pull {short_id(item)}` instead.",
                )
            )
        buffer = io.BytesIO()
        client.download(item.id, buffer)
        try:
            text = buffer.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            fail(
                SpiderError(
                    ErrorCode.bad_request,
                    f"{item.name} is not valid UTF-8 text. "
                    f"Use `spider pull {short_id(item)}` instead.",
                )
            )
        write_clipboard(text)
    except SpiderError as exc:
        fail(exc)
    out.print(f"pasted {humanize_size(item.size)} from {short_id(item)}  {item.name}")
