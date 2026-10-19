"""The GUI's button logic, tested against the real app with no display."""

import hashlib
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from spider.cli.client import SpiderClient
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Item, ItemKind
from spider.gui import actions
from spider.server.app import create_app
from spider.server.storage import Storage

TOKEN = "g" * MIN_TOKEN_LENGTH


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


@pytest.fixture
def transport(store):
    return TestClient(create_app(store, TOKEN))._transport


@pytest.fixture
def api(transport):
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    client = SpiderClient(config, transport=transport)
    yield client
    client.close()


@pytest.fixture
def other_api(transport):
    """A second machine talking to the same server."""
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="park-macbook")
    client = SpiderClient(config, transport=transport)
    yield client
    client.close()


def test_clipboard_label_uses_the_first_line():
    assert actions.clipboard_label("  first line\nsecond") == "first line"
    assert actions.clipboard_label("a   b \t c") == "a b c"
    assert actions.clipboard_label("x" * 100) == "x" * 40
    assert actions.clipboard_label("   ") == "clipboard"


def test_send_text_then_fetch_text_roundtrips_thai_unchanged(api):
    original = "ข้อความยาวๆ\r\nบรรทัดสอง\tแท็บ 🕸️"
    sent = actions.send_text(api, original)
    assert sent.kind is ItemKind.text
    item, text = actions.fetch_text(api, "latest")
    assert item.id == sent.id
    assert text == original


def test_send_text_rejects_an_empty_clipboard(api):
    with pytest.raises(SpiderError) as caught:
        actions.send_text(api, "  \n")
    assert caught.value.code is ErrorCode.bad_request
    assert api.list_items().items == []


def test_fetch_text_refuses_a_file(api, tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\x00\x01")
    actions.send_files(api, [path])
    with pytest.raises(SpiderError) as caught:
        actions.fetch_text(api, "latest")
    assert "Save as" in caught.value.message


def test_fetch_text_refuses_invalid_utf8(api):
    api.push_bytes(b"\xff\xfe\x00bad", kind=ItemKind.text, name="broken")
    with pytest.raises(SpiderError) as caught:
        actions.fetch_text(api, "latest")
    assert "UTF-8" in caught.value.message


def test_send_files_sends_every_file(api, tmp_path):
    paths = []
    for name in ("a.bin", "b.bin"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        paths.append(path)
    items = actions.send_files(api, paths)
    assert [item.name for item in items] == ["a.bin", "b.bin"]


def test_send_files_refuses_a_folder(api, tmp_path):
    with pytest.raises(SpiderError):
        actions.send_files(api, [tmp_path])


def test_save_item_writes_exact_bytes_and_leaves_no_part_file(api, tmp_path):
    payload = bytes(range(256)) * 10
    pushed = api.push_bytes(payload, kind=ItemKind.file, name="all.bin")
    target = tmp_path / "out" / "all.bin"
    saved, warning = actions.save_item(api, pushed.id, target)
    assert warning is None
    assert target.read_bytes() == payload
    assert saved.sha256 == hashlib.sha256(payload).hexdigest()
    assert not list(target.parent.glob("*.part"))


def test_save_item_keeps_a_good_file_when_the_download_is_corrupt(api, store, tmp_path):
    pushed = api.push_bytes(b"original", kind=ItemKind.file, name="x.bin")
    store.blob_path(pushed.id).write_bytes(b"tampered")
    target = tmp_path / "x.bin"
    target.write_bytes(b"existing good copy")
    with pytest.raises(SpiderError) as caught:
        actions.save_item(api, pushed.id, target)
    assert caught.value.code is ErrorCode.checksum_mismatch
    assert target.read_bytes() == b"existing good copy"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("stored", ["../../.bashrc", "/etc/passwd", ".."])
def test_suggested_filename_never_escapes(api, stored):
    item = api.push_bytes(b"x", kind=ItemKind.file, name=stored)
    assert "/" not in actions.suggested_filename(item)
    assert actions.suggested_filename(item) not in ("", ".", "..")


def test_list_and_delete(api):
    first = actions.send_text(api, "invoice text")
    actions.send_text(api, "unrelated")
    assert [i.id for i in actions.list_items(api, "invoice")] == [first.id]
    actions.delete_item(api, first.id)
    assert [i.name for i in actions.list_items(api, None)] == ["unrelated"]


@pytest.mark.parametrize(
    "name,ctype,kind,expected",
    [
        ("a.py", "text/x-python", ItemKind.file, "python"),
        ("b.tar.gz", "application/gzip", ItemKind.file, "zip"),
        ("c.JPG", "image/jpeg", ItemKind.file, "image"),
        ("noext", "audio/mpeg", ItemKind.file, "audio"),
        ("notes", "text/plain; charset=utf-8", ItemKind.text, "text"),
        ("x.weird", "application/octet-stream", ItemKind.file, "file"),
        ("see main.py", "text/plain; charset=utf-8", ItemKind.text, "text"),
        ("x.weird", "image/x-foo", ItemKind.file, "image"),
    ],
)
def test_icon_name(name, ctype, kind, expected):
    assert actions.icon_name(name, ctype, kind) == expected


def test_every_icon_name_has_an_svg():
    for stem in {
        *actions.ICON_BY_EXTENSION.values(),
        *actions.ICON_BY_FAMILY.values(),
        "text",
        "file",
    }:
        assert (actions.ICON_DIR / f"{stem}.svg").is_file(), stem


def test_icons_ship_with_a_license():
    assert (actions.ICON_DIR / "LICENSE").is_file()


@pytest.mark.parametrize(
    "existing,name,expected",
    [
        ([], "report.pdf", "report.pdf"),
        (["report.pdf"], "report.pdf", "report (1).pdf"),
        (["report.pdf", "report (1).pdf"], "report.pdf", "report (2).pdf"),
        (["a.tar.gz"], "a.tar.gz", "a (1).tar.gz"),
        (["noext"], "noext", "noext (1)"),
        ([".bashrc"], ".bashrc", ".bashrc (1)"),
    ],
)
def test_unique_path(tmp_path, existing, name, expected):
    for each in existing:
        (tmp_path / each).write_bytes(b"x")
    assert actions.unique_path(tmp_path, name) == tmp_path / expected


def test_receive_saves_into_downloads_without_overwriting(api, tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"original")
    pushed = api.push_bytes(b"new bytes", kind=ItemKind.file, name="report.pdf")
    received = actions.receive(api, pushed.id, tmp_path)
    assert received.path == tmp_path / "report (1).pdf"
    assert received.path.read_bytes() == b"new bytes"
    assert (tmp_path / "report.pdf").read_bytes() == b"original"
    assert received.text is None
    assert received.warning is None


def test_receive_creates_the_downloads_folder(api, tmp_path):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="a.bin")
    received = actions.receive(api, pushed.id, tmp_path / "missing")
    assert received.path == tmp_path / "missing" / "a.bin"
    assert received.path.is_file()


def test_receive_text_returns_text_and_records_clipboard(api, tmp_path):
    sent = actions.send_text(api, "สวัสดี")
    downloads = tmp_path / "downloads"
    received = actions.receive(api, sent.id, downloads)
    assert received.text == "สวัสดี"
    assert received.path is None
    assert [d.destination for d in api.history(sent.id).downloads] == ["clipboard"]
    assert not downloads.exists()


def test_receive_records_the_saved_path_in_history(api, tmp_path):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="a.bin")
    received = actions.receive(api, pushed.id, tmp_path)
    downloads = api.history(pushed.id).downloads
    assert [d.destination for d in downloads] == [str(received.path.resolve())]
    assert downloads[0].device == "gui-test"


def test_receive_of_a_deleted_item_raises_not_found(api, tmp_path):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="a.bin")
    api.delete(pushed.id)
    with pytest.raises(SpiderError) as caught:
        actions.receive(api, pushed.id, tmp_path)
    assert caught.value.code is ErrorCode.not_found


def test_a_failed_report_does_not_fail_the_save(api, tmp_path, monkeypatch):
    def refuse(item_id, destination):
        raise SpiderError(ErrorCode.unreachable, "history is down")

    monkeypatch.setattr(api, "report_download", refuse)
    pushed = api.push_bytes(b"keep me", kind=ItemKind.file, name="a.bin")
    received = actions.receive(api, pushed.id, tmp_path)
    assert received.path.read_bytes() == b"keep me"
    assert "history is down" in received.warning


def _item(name, kind, source, size=10) -> Item:
    return Item(
        id=f"id-{name}",
        kind=kind,
        name=name,
        size=size,
        sha256="0" * 64,
        content_type="application/octet-stream",
        created_at=datetime.now(UTC),
        source_device=source,
    )


def test_group_notices_groups_files_but_not_texts():
    items = [
        _item("a.pdf", ItemKind.file, "park-macbook"),
        _item("note one", ItemKind.text, "park-macbook"),
        _item("b.pdf", ItemKind.file, "park-macbook"),
        _item("note two", ItemKind.text, "park-macbook"),
        _item("c.pdf", ItemKind.file, "park-macbook"),
    ]
    notices = actions.group_notices(items)
    assert len(notices) == 3
    files = next(n for n in notices if len(n.item_ids) == 3)
    assert files.message == "3 files from park-macbook — click to receive all"
    assert files.title == "spider · from park-macbook"
    assert sum(1 for n in notices if len(n.item_ids) == 1) == 2


def test_group_notices_single_file_and_sender_order():
    items = [
        _item("x.bin", ItemKind.file, "zeta", size=1),
        _item("y.bin", ItemKind.file, "alpha", size=2048),
        _item("z.bin", ItemKind.file, "alpha", size=1),
    ]
    notices = actions.group_notices(items)
    assert [n.title for n in notices] == ["spider · from zeta", "spider · from alpha"]
    assert notices[0].message == "x.bin (1 B) — click to save to Downloads"
    assert notices[1].message == "2 files from alpha — click to receive all"


def test_poll_acks_so_the_next_poll_is_empty(api, other_api):
    api.heartbeat()
    other_api.push_bytes(b"x", kind=ItemKind.file, name="report.pdf", to_device="gui-test")
    first = actions.poll(api)
    assert {d.name for d in first.devices} >= {"gui-test"}
    assert [n.message.split(" — ")[1] for n in first.notices] == ["click to save to Downloads"]
    assert first.notices[0].title == "spider · from park-macbook"
    assert actions.poll(api).notices == []


def test_poll_against_an_old_server_returns_no_devices():
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    old = httpx.MockTransport(lambda request: httpx.Response(404, text="Not Found"))
    client = SpiderClient(config, transport=old)
    assert actions.poll(client) == actions.Poll(None, [])


def test_send_text_and_files_to_a_device_land_in_its_inbox(api, other_api, tmp_path):
    other_api.heartbeat()
    path = tmp_path / "a.txt"
    path.write_bytes(b"a")
    actions.send_text(api, "for you", to_device="park-macbook")
    actions.send_files(api, [path], to_device="park-macbook")
    assert sorted(item.name for item in other_api.inbox()) == ["a.txt", "for you"]


def test_load_history_lists_the_downloads(api):
    item = actions.send_text(api, "history please")
    actions.fetch_text(api, item.id)
    history = actions.load_history(api, item.id)
    assert [d.destination for d in history.downloads] == ["clipboard"]


def test_load_history_is_none_against_an_old_server():
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    old = httpx.MockTransport(lambda request: httpx.Response(404, text="Not Found"))
    client = SpiderClient(config, transport=old)
    assert actions.load_history(client, "abc") is None


def test_load_history_of_a_deleted_item_still_raises(api):
    with pytest.raises(SpiderError) as caught:
        actions.load_history(api, "0" * 26)
    assert caught.value.code is ErrorCode.not_found
