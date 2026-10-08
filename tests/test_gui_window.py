"""Drive the real window, offscreen, against the real app."""

import os
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
def window(qapp, store):
    config = ClientConfig(server="http://spider.test", token=TOKEN, device="gui-test")
    transport = TestClient(create_app(store, TOKEN))._transport
    win = MainWindow(lambda: SpiderClient(config, transport=transport), lambda _parent: False)
    yield win
    win._poll.stop()
    QThreadPool.globalInstance().waitForDone(5000)
    win._quitting = True
    win.close()


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
    return [window.table.item(row, 0).text() for row in range(window.table.rowCount())]


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
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
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
    import httpx

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
