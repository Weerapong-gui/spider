"""The GUI's button logic, tested against the real app with no display."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from spider.cli.client import SpiderClient
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import ItemKind
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
def api(store):
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    client = SpiderClient(config, transport=TestClient(create_app(store, TOKEN))._transport)
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
    saved = actions.save_item(api, pushed.id, target)
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
