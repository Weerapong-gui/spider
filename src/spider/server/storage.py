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

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind, new_ulid

PREVIEW_CHARS = 200
_PREVIEW_BYTES = PREVIEW_CHARS * 4  # worst case for UTF-8

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
