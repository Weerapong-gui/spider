import hashlib
import io
import os
import struct
import zlib

import pytest
from PIL import Image

from spider.core.models import ItemKind
from spider.server import thumbnails
from spider.server.storage import Storage


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


def _image_bytes(fmt, size=(800, 400), **save_args):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, fmt, **save_args)
    return buffer.getvalue()


def _save(store, data, content_type, name="file"):
    return store.save(
        stream=[data],
        kind=ItemKind.file,
        name=name,
        content_type=content_type,
        source_device="laptop",
    )


def test_png_thumbnail_is_a_png_no_larger_than_96(store):
    item = _save(store, _image_bytes("PNG"), "image/png")
    path = store.thumbnail(item.id)
    assert path == store.thumb_path(item.id)
    with Image.open(path) as thumb:
        assert thumb.format == "PNG"
        assert thumb.size == (96, 48)


def test_content_type_parameters_and_case_are_ignored(store):
    item = _save(store, _image_bytes("PNG"), "Image/PNG; charset=binary")
    assert store.thumbnail(item.id) is not None


def test_thumbnail_does_not_touch_the_blob(store):
    item = _save(store, _image_bytes("PNG"), "image/png")
    before = hashlib.sha256(store.blob_path(item.id).read_bytes()).hexdigest()
    store.thumbnail(item.id)
    after = hashlib.sha256(store.blob_path(item.id).read_bytes()).hexdigest()
    assert after == before == item.sha256


def test_exif_orientation_is_applied(store):
    exif = Image.Exif()
    exif[0x0112] = 6
    item = _save(store, _image_bytes("JPEG", (100, 50), exif=exif), "image/jpeg")
    with Image.open(store.thumbnail(item.id)) as thumb:
        assert thumb.size[1] > thumb.size[0]


def test_gif_uses_the_first_frame(store):
    first = Image.new("RGB", (200, 100), "red")
    second = Image.new("RGB", (200, 100), "blue")
    buffer = io.BytesIO()
    first.save(buffer, "GIF", save_all=True, append_images=[second])
    item = _save(store, buffer.getvalue(), "image/gif")
    with Image.open(store.thumbnail(item.id)) as thumb:
        red, _green, blue, _alpha = thumb.getpixel((10, 10))
        assert red > 200 and blue < 50


def test_pdf_has_no_thumbnail(store):
    item = _save(store, b"%PDF-1.4", "application/pdf")
    assert store.thumbnail(item.id) is None
    assert not store.thumb_path(item.id).exists()


def test_missing_blob_has_no_thumbnail(store):
    item = _save(store, _image_bytes("PNG"), "image/png")
    store.blob_path(item.id).unlink()
    assert store.thumbnail(item.id) is None


def test_thumbnail_of_a_truncated_jpeg_is_none_and_not_retried(store, monkeypatch):
    data = _image_bytes("JPEG")
    bad = _save(store, data[: len(data) // 2], "image/jpeg")
    assert store.thumbnail(bad.id) is None
    assert store.thumb_path(bad.id).stat().st_size == 0

    def boom(*_args):
        raise AssertionError("render must not be retried")

    monkeypatch.setattr(thumbnails, "render", boom)
    assert store.thumbnail(bad.id) is None


def _png_claiming(width, height):
    def chunk(tag, body):
        crc = zlib.crc32(tag + body)
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def test_thumbnail_of_a_pixel_bomb_is_none(store):
    item = _save(store, _png_claiming(10000, 10000), "image/png")
    assert store.thumbnail(item.id) is None


def test_render_returns_false_for_a_bomb_without_raising(tmp_path):
    source = tmp_path / "bomb.png"
    source.write_bytes(_png_claiming(10000, 10000))
    assert thumbnails.render(source, tmp_path / "out.png") is False


def test_source_over_50_mb_is_not_rendered(store, monkeypatch):
    item = _save(store, _image_bytes("PNG"), "image/png")
    monkeypatch.setattr(thumbnails, "MAX_SOURCE_BYTES", 10)
    assert store.thumbnail(item.id) is None


def test_second_call_reuses_the_cached_thumbnail(store, monkeypatch):
    item = _save(store, _image_bytes("PNG"), "image/png")
    first = store.thumbnail(item.id)
    monkeypatch.setattr(thumbnails, "render", lambda *_a: pytest.fail("re-rendered"))
    assert store.thumbnail(item.id) == first


def test_delete_and_gc_remove_thumbnails(store):
    gone = _save(store, _image_bytes("PNG"), "image/png")
    orphan = _save(store, _image_bytes("PNG"), "image/png")
    assert store.thumbnail(gone.id) is not None
    assert store.thumbnail(orphan.id) is not None

    store.delete(gone.id)
    assert not store.thumb_path(gone.id).exists()

    # An orphan thumbnail (row gone behind our back) is swept once old enough.
    connection = store.connect()
    try:
        connection.execute("DELETE FROM items WHERE id = ?", (orphan.id,))
        connection.commit()
    finally:
        connection.close()
    store.gc(max_age_seconds=3600)
    assert store.thumb_path(orphan.id).exists()
    old = os.stat(store.thumb_path(orphan.id)).st_mtime - 7200
    os.utime(store.thumb_path(orphan.id), (old, old))
    store.gc(max_age_seconds=3600)
    assert not store.thumb_path(orphan.id).exists()


def test_a_tiff_labelled_png_gets_no_thumbnail(store):
    item = _save(store, _image_bytes("TIFF"), "image/png")
    assert store.thumbnail(item.id) is None
