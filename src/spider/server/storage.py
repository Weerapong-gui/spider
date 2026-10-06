"""Blob files on disk plus a SQLite index.

This is the only module that touches the filesystem or the database. Keeping
that in one place is what makes the byte-exactness guarantee checkable: there
is exactly one function that writes bytes and one that reads them back.
"""

from __future__ import annotations

import errno
import hashlib
import secrets
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind, ItemPage, new_ulid

PREVIEW_CHARS = 200
_PREVIEW_BYTES = PREVIEW_CHARS * 4  # worst case for UTF-8
_ID_ALPHABET = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
_MAX_LIMIT = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_device TEXT NOT NULL,
  preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
"""


class Storage:
    def __init__(
        self,
        data_dir: Path,
        min_free_gb: float = 10.0,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.blobs_dir = self.data_dir / "blobs"
        self.tmp_dir = self.blobs_dir / "tmp"
        self.db_path = self.data_dir / "db.sqlite"
        self.min_free_bytes = int(min_free_gb * 1024**3)
        self.chunk_size = chunk_size

    def init(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def blob_path(self, item_id: str) -> Path:
        return self.blobs_dir / item_id[0:2] / item_id[2:4] / item_id

    def tmp_path(self, item_id: str) -> Path:
        return self.tmp_dir / f"{item_id}.part"

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.data_dir).free

    def _require_free_space(self) -> None:
        if self.free_bytes() < self.min_free_bytes:
            raise SpiderError(
                ErrorCode.disk_full,
                "Not enough free disk space on the server. Delete some items and retry.",
            )

    @staticmethod
    def _build_preview(kind: ItemKind, head: bytes) -> str | None:
        if kind is not ItemKind.text:
            return None
        return head.decode("utf-8", errors="ignore")[:PREVIEW_CHARS]

    def save(
        self,
        *,
        stream: Iterable[bytes],
        kind: ItemKind,
        name: str,
        content_type: str,
        source_device: str,
        expected_sha256: str | None = None,
    ) -> Item:
        """Stream bytes to disk, then publish them atomically.

        Order matters. Bytes land in `blobs/tmp/` first, move into place with a
        single rename, and only then get a database row. A crash at any point
        leaves either nothing or a complete blob with no row -- never a
        half-written file that something else can read.
        """
        self._require_free_space()

        item_id = new_ulid()
        temporary = self.tmp_path(item_id)
        digest = hashlib.sha256()
        size = 0
        head = bytearray()

        try:
            with temporary.open("wb") as handle:
                for chunk in stream:
                    if not chunk:
                        continue
                    handle.write(chunk)
                    handle.flush()
                    digest.update(chunk)
                    size += len(chunk)
                    if len(head) < _PREVIEW_BYTES:
                        head.extend(chunk[: _PREVIEW_BYTES - len(head)])

            checksum = digest.hexdigest()
            if expected_sha256 and not secrets.compare_digest(
                checksum, expected_sha256.strip().lower()
            ):
                raise SpiderError(
                    ErrorCode.checksum_mismatch,
                    f"Checksum mismatch: server computed {checksum},"
                    f" client sent {expected_sha256}.",
                )

            final = self.blob_path(item_id)
            final.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(final)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            if exc.errno == errno.ENOSPC:
                raise SpiderError(ErrorCode.disk_full, "The server ran out of disk space.") from exc
            raise
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        item = Item(
            id=item_id,
            kind=kind,
            name=name,
            size=size,
            sha256=checksum,
            content_type=content_type,
            created_at=datetime.now(UTC),
            source_device=source_device,
            preview=self._build_preview(kind, bytes(head)),
        )
        connection = self.connect()
        try:
            connection.execute(
                "INSERT INTO items (id, kind, name, size, sha256, content_type,"
                " created_at, source_device, preview) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item.id,
                    item.kind.value,
                    item.name,
                    item.size,
                    item.sha256,
                    item.content_type,
                    item.created_at.isoformat(),
                    item.source_device,
                    item.preview,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return item

    @staticmethod
    def _row_to_item(row) -> Item:
        return Item(
            id=row["id"],
            kind=ItemKind(row["kind"]),
            name=row["name"],
            size=row["size"],
            sha256=row["sha256"],
            content_type=row["content_type"],
            created_at=datetime.fromisoformat(row["created_at"]),
            source_device=row["source_device"],
            preview=row["preview"],
        )

    def get(self, item_id: str) -> Item:
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise SpiderError(ErrorCode.not_found, f"No item with id {item_id}.")
        return self._row_to_item(row)

    def resolve(self, ref: str) -> Item:
        """Turn a user-typed reference into one item.

        Doing this on the server keeps the CLI and the web UI in agreement
        without either of them having to fetch a listing and match locally.
        """
        ref = ref.strip()
        connection = self.connect()
        try:
            if ref.lower() == "latest":
                row = connection.execute("SELECT * FROM items ORDER BY id DESC LIMIT 1").fetchone()
                if row is None:
                    raise SpiderError(ErrorCode.not_found, "There are no items yet.")
                return self._row_to_item(row)

            prefix = ref.upper()
            # ULIDs use a fixed alphabet, so anything outside it cannot match.
            # Checking here also means no LIKE metacharacter ever reaches SQL.
            if not prefix or not set(prefix) <= _ID_ALPHABET:
                raise SpiderError(ErrorCode.not_found, f"No item matching {ref!r}.")

            rows = connection.execute(
                "SELECT * FROM items WHERE id LIKE ? ORDER BY id DESC LIMIT 2",
                (prefix + "%",),
            ).fetchall()
        finally:
            connection.close()

        if not rows:
            raise SpiderError(ErrorCode.not_found, f"No item matching {ref!r}.")
        if len(rows) > 1:
            candidates = ", ".join(row["id"] for row in rows)
            raise SpiderError(
                ErrorCode.ambiguous_id,
                f"{ref!r} matches more than one item: {candidates}. Use more characters.",
            )
        return self._row_to_item(rows[0])

    def list_items(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        q: str | None = None,
        kind: ItemKind | None = None,
    ) -> ItemPage:
        limit = max(1, min(int(limit), _MAX_LIMIT))
        clauses: list[str] = []
        params: list[object] = []

        if before:
            clauses.append("id < ?")
            params.append(before.strip().upper())
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append("(name LIKE ? ESCAPE '\\' OR IFNULL(preview, '') LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self.connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM items {where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                (*params, limit + 1),
            ).fetchall()
        finally:
            connection.close()

        items = [self._row_to_item(row) for row in rows[:limit]]
        next_before = items[-1].id if len(rows) > limit and items else None
        return ItemPage(items=items, next_before=next_before)

    def open_blob(self, item_id: str) -> BinaryIO:
        path = self.blob_path(item_id)
        if not path.is_file():
            raise SpiderError(
                ErrorCode.not_found,
                f"The stored file for {item_id} is missing. Run `spider verify`.",
            )
        return path.open("rb")

    def delete(self, item_id: str) -> None:
        """Drop the row first, then the blob.

        A crash between the two leaves an orphan blob, which garbage collection
        removes -- never a row pointing at a file that is gone.
        """
        item = self.get(item_id)
        connection = self.connect()
        try:
            connection.execute("DELETE FROM items WHERE id = ?", (item.id,))
            connection.commit()
        finally:
            connection.close()
        self.blob_path(item.id).unlink(missing_ok=True)
