import hashlib
import sqlite3

import pytest

from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import ItemKind
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



def chunks(payload: bytes, size: int = 7):
    for offset in range(0, len(payload), size):
        yield payload[offset : offset + size]


def save_bytes(store, payload: bytes, *, kind=ItemKind.file, name="thing.bin", **kwargs):
    return store.save(
        stream=chunks(payload),
        kind=kind,
        name=name,
        content_type="application/octet-stream",
        source_device="test-device",
        **kwargs,
    )


def test_save_writes_the_blob_and_returns_a_complete_item(store):
    payload = b"hello spider"
    item = save_bytes(store, payload)
    assert item.size == len(payload)
    assert item.sha256 == hashlib.sha256(payload).hexdigest()
    assert item.source_device == "test-device"
    assert store.blob_path(item.id).read_bytes() == payload


def test_save_inserts_exactly_one_row(store):
    save_bytes(store, b"x")
    connection = store.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    finally:
        connection.close()


def test_save_leaves_no_temporary_file_behind(store):
    save_bytes(store, b"x" * 5000)
    assert list(store.tmp_dir.iterdir()) == []


def test_save_writes_incrementally_instead_of_buffering(store):
    """Watch the temporary file grow while the stream is still being consumed.

    An implementation that collected every chunk before writing would leave the
    file at zero bytes until the generator was exhausted.
    """
    sizes = []

    def probing_stream():
        for _ in range(4):
            yield b"z" * 4096
            partials = list(store.tmp_dir.glob("*.part"))
            sizes.append(partials[0].stat().st_size if partials else 0)

    item = store.save(
        stream=probing_stream(),
        kind=ItemKind.file,
        name="big.bin",
        content_type="application/octet-stream",
        source_device="test-device",
    )
    assert item.size == 4 * 4096
    assert sizes[0] > 0, "nothing was written until the stream ended"
    assert sizes == sorted(sizes)
    assert sizes[-1] > sizes[0]


def test_save_verifies_a_supplied_checksum(store):
    payload = b"trustworthy"
    item = save_bytes(store, payload, expected_sha256=hashlib.sha256(payload).hexdigest())
    assert item.sha256 == hashlib.sha256(payload).hexdigest()


def test_save_rejects_a_wrong_checksum(store):
    with pytest.raises(SpiderError) as caught:
        save_bytes(store, b"real bytes", expected_sha256="f" * 64)
    assert caught.value.code is ErrorCode.checksum_mismatch


def test_a_rejected_checksum_leaves_nothing_behind(store):
    with pytest.raises(SpiderError):
        save_bytes(store, b"real bytes", expected_sha256="f" * 64)
    assert list(store.tmp_dir.iterdir()) == []
    connection = store.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        connection.close()


def test_a_failing_stream_leaves_nothing_behind(store):
    def exploding():
        yield b"partial"
        raise OSError("cable unplugged")

    with pytest.raises(OSError, match="cable unplugged"):
        store.save(
            stream=exploding(),
            kind=ItemKind.file,
            name="doomed.bin",
            content_type="application/octet-stream",
            source_device="test-device",
        )
    assert list(store.tmp_dir.iterdir()) == []
    connection = store.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        connection.close()


def test_save_refuses_when_free_space_is_below_the_floor(tmp_path):
    store = Storage(tmp_path / "data", min_free_gb=1_000_000)
    store.init()
    with pytest.raises(SpiderError) as caught:
        save_bytes(store, b"anything")
    assert caught.value.code is ErrorCode.disk_full


def test_text_items_get_a_preview(store):
    item = save_bytes(store, "สวัสดี ครับ".encode(), kind=ItemKind.text, name="greeting")
    assert item.preview == "สวัสดี ครับ"


def test_preview_is_capped_and_never_splits_a_character(store):
    payload = ("ก" * 500).encode()
    item = save_bytes(store, payload, kind=ItemKind.text, name="long")
    assert len(item.preview) == 200
    assert item.preview == "ก" * 200


def test_file_items_have_no_preview(store):
    item = save_bytes(store, b"\x00\x01\x02")
    assert item.preview is None


def test_empty_payload_is_stored(store):
    item = save_bytes(store, b"")
    assert item.size == 0
    assert item.sha256 == hashlib.sha256(b"").hexdigest()
    assert store.blob_path(item.id).read_bytes() == b""
