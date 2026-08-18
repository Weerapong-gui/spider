import sqlite3

import pytest

from spider.server.storage import Storage


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


def test_init_creates_the_directory_layout(store, tmp_path):
    root = tmp_path / "data"
    assert (root / "blobs").is_dir()
    assert (root / "blobs" / "tmp").is_dir()
    assert (root / "db.sqlite").is_file()


def test_init_is_idempotent(store):
    store.init()
    store.init()


def test_schema_has_the_expected_columns(store):
    connection = store.connect()
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(items)")}
    finally:
        connection.close()
    assert columns == {
        "id", "kind", "name", "size", "sha256",
        "content_type", "created_at", "source_device", "preview",
    }


def test_write_ahead_logging_is_enabled(store):
    connection = store.connect()
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        connection.close()
    assert mode.lower() == "wal"


def test_blob_path_shards_two_levels_deep(store, tmp_path):
    path = store.blob_path("01JD3K7XABCDEFGHJKMNPQRSTV")
    assert path == tmp_path / "data" / "blobs" / "01" / "JD" / "01JD3K7XABCDEFGHJKMNPQRSTV"


def test_tmp_path_is_inside_the_tmp_directory(store, tmp_path):
    assert store.tmp_path("01JD3K7X").parent == tmp_path / "data" / "blobs" / "tmp"
    assert store.tmp_path("01JD3K7X").name.endswith(".part")


def test_free_bytes_is_positive(store):
    assert store.free_bytes() > 0


def test_connect_returns_a_usable_connection(store):
    connection = store.connect()
    try:
        assert isinstance(connection, sqlite3.Connection)
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        connection.close()
