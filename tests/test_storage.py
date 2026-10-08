import hashlib
import os
import sqlite3
import time as time_module
from datetime import UTC, datetime, timedelta

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
        "id",
        "kind",
        "name",
        "size",
        "sha256",
        "content_type",
        "created_at",
        "source_device",
        "preview",
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


def test_get_returns_the_saved_item(store):
    saved = save_bytes(store, b"abc", name="abc.bin")
    assert store.get(saved.id) == saved


def test_get_raises_not_found_for_an_unknown_id(store):
    with pytest.raises(SpiderError) as caught:
        store.get("01JD3K7XABCDEFGHJKMNPQRSTV")
    assert caught.value.code is ErrorCode.not_found


def test_resolve_latest_returns_the_newest_item(store):
    save_bytes(store, b"first", name="first")
    newest = save_bytes(store, b"second", name="second")
    assert store.resolve("latest").id == newest.id


def test_resolve_latest_on_an_empty_store_raises_not_found(store):
    with pytest.raises(SpiderError) as caught:
        store.resolve("latest")
    assert caught.value.code is ErrorCode.not_found


def test_resolve_accepts_a_full_id(store):
    saved = save_bytes(store, b"x")
    assert store.resolve(saved.id).id == saved.id


def test_resolve_accepts_a_lowercase_prefix(store):
    saved = save_bytes(store, b"x")
    assert store.resolve(saved.id[:8].lower()).id == saved.id


def test_resolve_reports_an_ambiguous_prefix(store):
    first = save_bytes(store, b"a")
    save_bytes(store, b"b")
    with pytest.raises(SpiderError) as caught:
        store.resolve(first.id[:4])
    assert caught.value.code is ErrorCode.ambiguous_id


def test_resolve_rejects_characters_that_cannot_appear_in_an_id(store):
    save_bytes(store, b"x")
    with pytest.raises(SpiderError) as caught:
        store.resolve("%")
    assert caught.value.code is ErrorCode.not_found


def test_list_returns_newest_first(store):
    first = save_bytes(store, b"1", name="one")
    second = save_bytes(store, b"2", name="two")
    page = store.list_items()
    assert [item.id for item in page.items] == [second.id, first.id]


def test_list_paginates_with_a_cursor(store):
    ids = [save_bytes(store, bytes([n]), name=f"n{n}").id for n in range(5)]
    first_page = store.list_items(limit=2)
    assert [item.id for item in first_page.items] == ids[4:2:-1]
    assert first_page.next_before == ids[3]
    second_page = store.list_items(limit=2, before=first_page.next_before)
    assert [item.id for item in second_page.items] == ids[2:0:-1]


def test_cursor_paging_is_unaffected_by_newly_added_items(store):
    ids = [save_bytes(store, bytes([n]), name=f"n{n}").id for n in range(4)]
    first_page = store.list_items(limit=2)
    save_bytes(store, b"new arrival", name="newcomer")
    second_page = store.list_items(limit=2, before=first_page.next_before)
    seen = [item.id for item in first_page.items] + [item.id for item in second_page.items]
    assert seen == ids[::-1]
    assert len(seen) == len(set(seen))


def test_list_exhausted_returns_no_cursor(store):
    save_bytes(store, b"only")
    assert store.list_items(limit=10).next_before is None


def test_list_filters_by_kind(store):
    save_bytes(store, b"a file")
    text = save_bytes(store, b"a note", kind=ItemKind.text, name="note")
    page = store.list_items(kind=ItemKind.text)
    assert [item.id for item in page.items] == [text.id]


def test_list_searches_name_and_preview(store):
    named = save_bytes(store, b"data", name="quarterly-invoice.pdf")
    inside = save_bytes(store, b"see the invoice attached", kind=ItemKind.text, name="memo")
    save_bytes(store, b"unrelated", name="cat.png")
    found = {item.id for item in store.list_items(q="invoice").items}
    assert found == {named.id, inside.id}


def test_search_treats_wildcards_literally(store):
    save_bytes(store, b"x", name="report.pdf")
    assert store.list_items(q="%").items == []


def test_open_blob_returns_the_exact_bytes(store):
    payload = bytes(range(256))
    saved = save_bytes(store, payload)
    with store.open_blob(saved.id) as handle:
        assert handle.read() == payload


def test_open_blob_raises_not_found_when_the_file_vanished(store):
    saved = save_bytes(store, b"x")
    store.blob_path(saved.id).unlink()
    with pytest.raises(SpiderError) as caught:
        store.open_blob(saved.id)
    assert caught.value.code is ErrorCode.not_found


def test_delete_removes_both_the_row_and_the_blob(store):
    saved = save_bytes(store, b"x")
    path = store.blob_path(saved.id)
    store.delete(saved.id)
    assert not path.exists()
    with pytest.raises(SpiderError):
        store.get(saved.id)


def test_delete_of_an_unknown_id_raises_not_found(store):
    with pytest.raises(SpiderError) as caught:
        store.delete("01JD3K7XABCDEFGHJKMNPQRSTV")
    assert caught.value.code is ErrorCode.not_found


def test_delete_succeeds_when_the_blob_is_already_gone(store):
    saved = save_bytes(store, b"x")
    store.blob_path(saved.id).unlink()
    store.delete(saved.id)
    with pytest.raises(SpiderError):
        store.get(saved.id)


def test_gc_removes_a_stale_partial_upload(store):
    stale = store.tmp_path("01JD3K7XABCDEFGHJKMNPQRSTV")
    stale.write_bytes(b"abandoned")
    old = time_module.time() - 7200
    os.utime(stale, (old, old))
    assert store.gc() == 1
    assert not stale.exists()


def test_gc_leaves_a_fresh_partial_upload_alone(store):
    fresh = store.tmp_path("01JD3K7XABCDEFGHJKMNPQRSTV")
    fresh.write_bytes(b"in progress")
    assert store.gc() == 0
    assert fresh.exists()


def test_gc_removes_an_orphaned_blob(store):
    saved = save_bytes(store, b"x")
    path = store.blob_path(saved.id)
    connection = store.connect()
    try:
        connection.execute("DELETE FROM items WHERE id = ?", (saved.id,))
        connection.commit()
    finally:
        connection.close()
    old = time_module.time() - 7200
    os.utime(path, (old, old))
    assert store.gc() == 1
    assert not path.exists()


def test_gc_never_touches_a_blob_that_has_a_row(store):
    saved = save_bytes(store, b"keep me")
    path = store.blob_path(saved.id)
    old = time_module.time() - 999999
    os.utime(path, (old, old))
    assert store.gc() == 0
    assert path.read_bytes() == b"keep me"


def test_verify_reports_nothing_for_a_healthy_store(store):
    save_bytes(store, b"a")
    save_bytes(store, b"b")
    assert store.verify() == []


def test_verify_reports_an_item_whose_blob_was_deleted_externally(store):
    healthy = save_bytes(store, b"a", name="healthy")
    broken = save_bytes(store, b"b", name="broken")
    store.blob_path(broken.id).unlink()
    reported = store.verify()
    assert [item.id for item in reported] == [broken.id]
    assert healthy.id not in {item.id for item in reported}


def _backdate(store, item_id: str, days: int) -> None:
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    connection = store.connect()
    try:
        connection.execute("UPDATE items SET created_at = ? WHERE id = ?", (when, item_id))
        connection.commit()
    finally:
        connection.close()


def test_retention_is_off_by_default(store):
    old = save_bytes(store, b"ancient", name="ancient")
    _backdate(store, old.id, 400)
    assert store.apply_retention(None) == 0
    assert store.apply_retention(0) == 0
    assert store.get(old.id).id == old.id


def test_retention_deletes_items_past_the_cutoff(store):
    old = save_bytes(store, b"ancient", name="ancient")
    recent = save_bytes(store, b"fresh", name="fresh")
    _backdate(store, old.id, 40)
    old_path = store.blob_path(old.id)

    assert store.apply_retention(30) == 1
    assert not old_path.exists()
    with pytest.raises(SpiderError):
        store.get(old.id)
    assert store.get(recent.id).id == recent.id
