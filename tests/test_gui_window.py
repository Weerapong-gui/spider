"""Drive the real window, offscreen, against the real app."""

import io
import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QMimeData,
    QPointF,
    Qt,
    QThreadPool,
    QUrl,
)
from PySide6.QtGui import QDropEvent, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from spider.cli.client import SpiderClient  # noqa: E402
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig  # noqa: E402
from spider.core.models import ItemKind  # noqa: E402
from spider.gui import actions  # noqa: E402
from spider.gui.window import MainWindow, SettingsDialog  # noqa: E402
from spider.server.app import create_app  # noqa: E402
from spider.server.storage import Storage  # noqa: E402

TOKEN = "w" * MIN_TOKEN_LENGTH


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


@pytest.fixture
def transport(store):
    return TestClient(create_app(store, TOKEN))._transport


def make_window(transport):
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    return MainWindow(lambda: SpiderClient(config, transport=transport), lambda _parent: False)


def close_window(win):
    win._poll.stop()
    QThreadPool.globalInstance().waitForDone(5000)
    win._quitting = True
    win.close()


@pytest.fixture
def window(qapp, transport):
    win = make_window(transport)
    yield win
    close_window(win)


@pytest.fixture
def other(transport):
    """A second machine talking to the same server."""
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="park-macbook")
    client = SpiderClient(config, transport=transport)
    yield client
    client.close()


class OldServer(httpx.BaseTransport):
    """The real app minus every route this feature added: an old server answers
    them from its static mount, with a plain-text 404 to GET and 405 to the rest."""

    def __init__(self, real: httpx.BaseTransport) -> None:
        self._real = real

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith(("/api/devices", "/api/inbox")) or path.endswith(
            ("/history", "/thumbnail")
        ):
            if request.method in ("GET", "HEAD"):
                return httpx.Response(404, text="Not Found")
            return httpx.Response(405, text="Method Not Allowed")
        return self._real.handle_request(request)


@pytest.fixture
def old_server_window(qapp, transport):
    win = make_window(OldServer(transport))
    yield win
    close_window(win)


def settle(window, condition, timeout=10.0):
    """Pump the event loop until `condition()` holds, so queued signals arrive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition() and not window._busy:
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the window")


def names(window):
    return [window.table.item(row, 1).text() for row in range(window.table.rowCount())]


def group(window, title):
    """(text, last-seen) of each row under one sidebar group."""
    for index in range(window.devices.topLevelItemCount()):
        top = window.devices.topLevelItem(index)
        if top.text(0) == title:
            return [(top.child(i).text(0), top.child(i).text(1)) for i in range(top.childCount())]
    raise AssertionError(f"no group {title}")


def device_row(window, name):
    for index in range(window.devices.topLevelItemCount()):
        top = window.devices.topLevelItem(index)
        for i in range(top.childCount()):
            if top.child(i).data(0, Qt.ItemDataRole.UserRole) == name:
                return top.child(i)
    return None


def history_lines(window):
    return [window.history.item(row).text() for row in range(window.history.count())]


def drop(window, paths, position):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)


def png_bytes():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(buffer, "PNG")
    return buffer.getvalue()


def test_starts_empty_with_a_hint(window):
    window.refresh()
    settle(window, lambda: True)
    assert window.table.rowCount() == 0
    assert not window.empty.isHidden()


def test_send_clipboard_shows_up_in_the_list(window):
    QGuiApplication.clipboard().setText("ข้อความจากคลิปบอร์ด")
    window.send_clipboard()
    settle(window, lambda: names(window) == ["ข้อความจากคลิปบอร์ด"])
    assert window.empty.isHidden()


def test_paste_latest_puts_the_text_on_the_clipboard(window):
    QGuiApplication.clipboard().setText("round trip\r\nline two")
    window.send_clipboard()
    settle(window, lambda: len(names(window)) == 1)
    QGuiApplication.clipboard().setText("something else")
    window.paste_latest()
    settle(window, lambda: QGuiApplication.clipboard().text() == "round trip\r\nline two")


def test_an_empty_clipboard_reports_an_error_and_sends_nothing(window):
    QGuiApplication.clipboard().setText("")
    window.send_clipboard()
    settle(window, lambda: "empty" in window.statusBar().currentMessage().lower())
    window.refresh()
    settle(window, lambda: True)
    assert window.table.rowCount() == 0


def test_dropping_files_sends_them(window, tmp_path):
    path = tmp_path / "dropped.bin"
    path.write_bytes(b"dropped bytes")
    drop(window, [path], QPointF(5, 5))
    settle(window, lambda: names(window) == ["dropped.bin"])


def test_delete_asks_first_and_removes_the_item(window, monkeypatch):
    QGuiApplication.clipboard().setText("to delete")
    window.send_clipboard()
    settle(window, lambda: len(names(window)) == 1)
    item = window._items[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    window.delete_item(item)
    window.refresh()
    settle(window, lambda: len(names(window)) == 1)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.delete_item(item)
    settle(window, lambda: names(window) == [])


def test_search_filters_the_list(window):
    for text in ("the invoice text", "unrelated"):
        QGuiApplication.clipboard().setText(text)
        window.send_clipboard()
        settle(window, lambda: True)
    settle(window, lambda: len(names(window)) == 2)
    window.search.setText("invoice")
    settle(window, lambda: names(window) == ["the invoice text"])


def test_an_unreachable_server_shows_an_error_instead_of_crashing(qapp):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    win = MainWindow(
        lambda: SpiderClient(config, transport=httpx.MockTransport(refuse)), lambda _p: False
    )
    try:
        win.refresh()
        settle(win, lambda: "Cannot reach" in win.statusBar().currentMessage())
    finally:
        win._poll.stop()
        QThreadPool.globalInstance().waitForDone(5000)
        win._quitting = True
        win.close()


def test_a_timed_out_poll_frees_the_slot_for_the_next_one(qapp, transport):
    """A stalled request ends in a timeout; the poll must then let the next one run,
    or heartbeats and notices stop for good."""
    stalls = [1]

    class StallOnce(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.path == "/api/devices/heartbeat" and stalls:
                stalls.pop()
                raise httpx.ReadTimeout("stalled", request=request)
            return transport.handle_request(request)

    win = make_window(StallOnce())
    try:
        win._start_poll()
        settle(win, lambda: not win._polling)
        assert win._server_ok is False
        win.refresh()
        settle(win, lambda: any(device.name == "gui-test" for device in win._devices))
        assert win._server_ok is True
    finally:
        close_window(win)


def test_settings_dialog_rejects_a_short_token(qapp):
    dialog = SettingsDialog(None, "laptop")
    dialog.server.setText("http://x:8181")
    dialog.token.setText("short")
    dialog._accept()
    assert "32" in dialog.problem.text()
    assert not hasattr(dialog, "result_config")


def test_settings_dialog_strips_pasted_whitespace(qapp):
    dialog = SettingsDialog(None, "laptop")
    dialog.server.setText(" http://x:8181/ \n")
    dialog.token.setText(TOKEN + "\n")
    dialog._accept()
    assert dialog.result_config == ClientConfig("http://x:8181", TOKEN, "laptop")


def test_callbacks_run_on_the_ui_thread(window):
    """Widgets may only be touched from the UI thread; a bare lambda connected
    to a worker's signal would run on the worker thread instead."""
    import threading

    seen = []
    window.run_task(
        lambda client: client.list_items(),
        lambda _result: seen.append(threading.current_thread()),
        "checking",
    )
    settle(window, lambda: bool(seen))
    assert seen == [threading.main_thread()]


def test_sidebar_groups_running_and_offline_devices(window, other, transport):
    other.heartbeat()
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="office-linux")
    with SpiderClient(config, transport=transport) as cli_only:
        cli_only.list_items()
    window.refresh()
    settle(window, lambda: device_row(window, "office-linux") is not None)
    assert [text for text, _ in group(window, "Server")] == ["spider.test"]
    running = [text for text, _ in group(window, "Running")]
    assert sorted(running) == ["gui-test (this device)", "park-macbook"]
    [(name, seen)] = group(window, "Offline")
    assert name == "office-linux"
    assert seen.endswith("ago")
    assert not window.devices.isHidden()


def test_sidebar_is_hidden_against_an_old_server(qapp, old_server_window):
    win = old_server_window
    QGuiApplication.clipboard().setText("from an old server")
    win.send_clipboard()
    settle(win, lambda: names(win) == ["from an old server"] and win.devices.isHidden())
    win.table.selectRow(0)
    settle(win, lambda: win.history.isHidden())
    assert "Error" not in win.statusBar().currentMessage()


def test_rows_are_tall_enough_for_a_48px_preview(window):
    QGuiApplication.clipboard().setText("a row")
    window.send_clipboard()
    settle(window, lambda: names(window) == ["a row"])
    assert window.table.iconSize().height() == 48
    assert window.table.verticalHeader().defaultSectionSize() == 52
    assert window.table.rowHeight(0) >= 52
    assert not window.table.item(0, 0).icon().isNull()


def test_a_png_row_gets_its_thumbnail(window, tmp_path):
    paths = [tmp_path / "photo.png", tmp_path / "other.png", tmp_path / "notes.txt"]
    for path in paths[:2]:
        path.write_bytes(png_bytes())
    paths[2].write_bytes(b"not an image")
    window.send_paths(paths)
    settle(window, lambda: sorted(names(window)) == ["notes.txt", "other.png", "photo.png"])
    images = {item.id for item in window._items if item.name.endswith(".png")}
    settle(window, lambda: images <= window._thumbs.keys())
    assert set(window._thumbs) == images  # only image rows are asked for
    for row, item in enumerate(window._items):
        if item.id in images:
            assert window.table.item(row, 0).icon().cacheKey() == window._thumbs[item.id].cacheKey()


def test_selecting_a_row_shows_its_history(window):
    assert history_lines(window) == ["Select an item to see its history"]
    QGuiApplication.clipboard().setText("with history")
    window.send_clipboard()
    settle(window, lambda: names(window) == ["with history"])
    window.paste_latest()
    settle(window, lambda: QGuiApplication.clipboard().text() == "with history")
    window.table.selectRow(0)
    settle(window, lambda: any("clipboard" in line for line in history_lines(window)))
    assert any("Uploaded from gui-test" in line for line in history_lines(window))
    window.table.clearSelection()
    settle(window, lambda: history_lines(window) == ["Select an item to see its history"])


def test_dropping_a_file_on_a_device_sends_it_there(window, other, tmp_path):
    other.heartbeat()
    window.show()
    window.refresh()
    settle(window, lambda: device_row(window, "park-macbook") is not None)
    row = device_row(window, "park-macbook")
    center = window.devices.visualItemRect(row).center()
    position = window.devices.viewport().mapTo(window, center)
    path = tmp_path / "for-the-mac.txt"
    path.write_bytes(b"hello mac")
    drop(window, [path], QPointF(position))
    settle(window, lambda: names(window) == ["for-the-mac.txt"])
    assert [item.name for item in other.inbox()] == ["for-the-mac.txt"]
    assert "no desktop app" not in window.statusBar().currentMessage()


def test_from_column_shows_the_target(window, other):
    other.list_items()  # known to the server, but no desktop app running
    window.refresh()
    settle(window, lambda: device_row(window, "park-macbook") is not None)
    QGuiApplication.clipboard().setText("for the mac")
    window.send_clipboard(to_device="park-macbook")
    settle(window, lambda: names(window) == ["for the mac"])
    assert window.table.item(0, 4).text() == "gui-test → park-macbook"
    assert window.statusBar().currentMessage() == (
        "park-macbook has no desktop app running; it will see this when it opens one."
    )


def known(window):
    """Let the server learn this device, so items can be sent to it."""
    window.refresh()
    settle(window, lambda: any(device.name == "gui-test" for device in window._devices))


def banner_texts(window):
    return [row.label.text() for row in window._notice_rows.values()]


def banner_button(window, text):
    [row] = window._notice_rows.values()
    assert row.action.text() == text
    return row.action


def test_an_item_sent_here_shows_a_banner_once(window, other):
    known(window)
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    assert banner_texts(window) == ["r.pdf (1 B) — click to save to Downloads"]
    assert window._last_notice.message == banner_texts(window)[0]
    assert "Receive r.pdf" in tray_entries(window)
    window.refresh()
    settle(window, lambda: True)
    QThreadPool.globalInstance().waitForDone(5000)
    QCoreApplication.processEvents()
    assert len(banner_texts(window)) == 1


def test_overlapping_refreshes_never_lose_or_repeat_a_notice(window, other, monkeypatch):
    """The poll acks what it fetches, so a refresh must never discard a poll's
    notices or let a second poll race it for the same inbox."""
    import threading

    lock = threading.Lock()
    running, most = [0], [0]
    real_poll = actions.poll

    def slow_poll(client):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        try:
            result = real_poll(client)
            time.sleep(0.2)  # acked, but not yet delivered: the refreshes below overlap it
            return result
        finally:
            with lock:
                running[0] -= 1

    known(window)
    monkeypatch.setattr(actions, "poll", slow_poll)
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    time.sleep(0.1)  # the first poll has acked the item by now
    for _ in range(2):
        window.refresh()
    window.search.setText("r")
    settle(window, lambda: bool(banner_texts(window)), timeout=3)
    for _ in range(2):
        window.refresh()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:  # let every queued and coalesced poll finish
        QThreadPool.globalInstance().waitForDone(5000)
        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert banner_texts(window) == ["r.pdf (1 B) — click to save to Downloads"]
    assert most[0] == 1


def test_notices_are_shown_even_when_drawing_the_devices_fails(window, other, monkeypatch):
    """The poll has acked them, so a failure elsewhere must not drop them."""
    known(window)

    def broken(_devices):
        raise RuntimeError("cannot draw the sidebar")

    monkeypatch.setattr(window, "show_devices", broken)
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    with window._make_client() as client:
        result = actions.poll(client)
    window._polling = True  # as if the window had started this poll
    with pytest.raises(RuntimeError):
        window._polled(result)
    assert banner_texts(window) == ["r.pdf (1 B) — click to save to Downloads"]
    assert not window._polling


def test_a_notice_already_pending_is_not_shown_again(window, other):
    known(window)
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    window._on_notices([window._last_notice])
    assert len(banner_texts(window)) == 1


def tray_entries(window):
    if window.tray is None:
        return [action.text() for action in window._receive_actions]
    return [action.text() for action in window.tray.contextMenu().actions()]


def test_banner_save_puts_the_file_in_downloads(window, other, tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(actions, "downloads_dir", lambda: downloads)
    (downloads).mkdir()
    (downloads / "r.pdf").write_bytes(b"already here")
    known(window)
    other.push_bytes(b"pdf bytes", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    banner_button(window, "Save to Downloads").click()
    settle(window, lambda: not banner_texts(window))
    assert (downloads / "r.pdf").read_bytes() == b"already here"
    assert (downloads / "r (1).pdf").read_bytes() == b"pdf bytes"
    assert window.statusBar().currentMessage() == f"Saved to {downloads / 'r (1).pdf'}"
    assert not any(text.startswith("Receive") for text in tray_entries(window))


def test_several_files_are_received_together(window, other, tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(actions, "downloads_dir", lambda: downloads)
    known(window)
    for name in ("a.txt", "b.txt"):
        other.push_bytes(name.encode(), kind=ItemKind.file, name=name, to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    assert banner_texts(window) == ["2 files from park-macbook — click to receive all"]
    window.receive_notice(window._last_notice)
    settle(window, lambda: not banner_texts(window))
    assert sorted(path.name for path in downloads.iterdir()) == ["a.txt", "b.txt"]


def test_banner_copy_puts_text_on_the_clipboard(window, other):
    QGuiApplication.clipboard().setText("before")
    known(window)
    other.push_bytes("สวัสดี".encode(), kind=ItemKind.text, name="greeting", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    assert banner_texts(window) == ["greeting — click to copy to the clipboard"]
    banner_button(window, "Copy").click()
    settle(window, lambda: QGuiApplication.clipboard().text() == "สวัสดี")
    settle(window, lambda: not banner_texts(window))
    assert window.statusBar().currentMessage() == "Copied “greeting” to the clipboard"


def test_receiving_a_deleted_item_says_so(window, other, store, tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "downloads_dir", lambda: tmp_path / "Downloads")
    known(window)
    item = other.push_bytes(b"gone", kind=ItemKind.file, name="gone.bin", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    other.delete(item.id)
    banner_button(window, "Save to Downloads").click()
    settle(window, lambda: "no longer" in window.statusBar().currentMessage())
    assert window.statusBar().currentMessage() == "“gone.bin” is no longer on the server"
    assert not banner_texts(window)


def test_dismiss_removes_the_banner_row(window, other):
    known(window)
    other.push_bytes(b"x", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    [row] = window._notice_rows.values()
    row.dismiss.click()
    assert banner_texts(window) == []
    assert window.banner.isHidden()


def test_quit_clears_the_heartbeat(window, store):
    window.refresh()
    settle(window, lambda: any(d.name == "gui-test" for d in window._devices))
    assert [d.online for d in store.list_devices() if d.name == "gui-test"] == [True]
    window.quit_for_real()
    QThreadPool.globalInstance().waitForDone(5000)
    assert [d.online for d in store.list_devices() if d.name == "gui-test"] == [False]


def test_a_second_click_while_receiving_does_not_fetch_again(window, other, tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(actions, "downloads_dir", lambda: downloads)
    calls = []
    real_receive = actions.receive

    def counting_receive(client, item_id, target):
        calls.append(item_id)
        return real_receive(client, item_id, target)

    # Two workers racing on one file can hide each other's output, so count the calls.
    monkeypatch.setattr(actions, "receive", counting_receive)
    known(window)
    item = other.push_bytes(b"once", kind=ItemKind.file, name="r.pdf", to_device="gui-test")
    window.refresh()
    settle(window, lambda: bool(banner_texts(window)))
    window.receive_notice(window._last_notice)
    window._notice_clicked()
    settle(window, lambda: not banner_texts(window))
    QThreadPool.globalInstance().waitForDone(5000)
    assert calls == [item.id]
    assert [path.name for path in downloads.iterdir()] == ["r.pdf"]
    assert len(other.history(item.id).downloads) == 1
