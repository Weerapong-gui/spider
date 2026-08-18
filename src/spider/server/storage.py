"""Blob files on disk plus a SQLite index.

This is the only module that touches the filesystem or the database. Keeping
that in one place is what makes the byte-exactness guarantee checkable: there
is exactly one function that writes bytes and one that reads them back.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

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
