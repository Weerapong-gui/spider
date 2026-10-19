"""The main window, the settings dialog, and the tray icon."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QPoint, QSize, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spider.cli.client import SpiderClient
from spider.cli.main import SHORT_ID_LENGTH, humanize_age, humanize_size
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import Device, Item, ItemHistory, ItemKind
from spider.gui import actions
from spider.gui.tasks import Task

REFRESH_MS = 10_000
COLUMNS = ("", "Name", "Kind", "Size", "From", "When", "ID")
PREVIEW_PX = 48
ROW_PX = 52
NO_SELECTION = "Select an item to see its history"
GREEN, RED, GREY = "#3fb950", "#d6455d", "#8b8f9a"
TRAY_RECEIVE_LIMIT = 5
# The endings `actions.group_notices` gives each kind of notice.
TEXT_ENDING = " — click to copy to the clipboard"
FILE_ENDING = " — click to save to Downloads"


def make_icon() -> QIcon:
    """A small drawn icon, so the package ships no image files."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7aa2f7"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setPen(QColor("#12131a"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(38)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()
    return QIcon(pixmap)


def dot_icon(color: str) -> QIcon:
    """A status light for the device sidebar."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(3, 3, 10, 10)
    painter.end()
    return QIcon(pixmap)


def local_time(when: datetime) -> str:
    local = when.astimezone()
    if local.date() == datetime.now().astimezone().date():
        return f"today {local:%H:%M}"
    return f"{local:%Y-%m-%d %H:%M}"


def notice_name(notice: actions.Notice) -> str | None:
    """The single item a notice names, read back from its copy; None for a group."""
    message = notice.message
    if message.endswith(TEXT_ENDING):
        return message.removesuffix(TEXT_ENDING)
    if message.endswith(FILE_ENDING):
        return message.removesuffix(FILE_ENDING).rpartition(" (")[0] or None
    return None


def is_text_notice(notice: actions.Notice) -> bool:
    # Texts are never grouped, so a text notice is exactly one text item.
    return notice.message.endswith(TEXT_ENDING)


class NoticeRow(QWidget):
    """One pending notice in the banner: what arrived, take it, or put it away."""

    def __init__(self, notice: actions.Notice, parent=None) -> None:
        super().__init__(parent)
        self.notice = notice
        self.label = QLabel(notice.message)
        self.label.setToolTip(notice.title)
        self.action = QPushButton("Copy" if is_text_notice(notice) else "Save to Downloads")
        self.dismiss = QPushButton("Dismiss")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.action)
        layout.addWidget(self.dismiss)


def history_lines(history: ItemHistory) -> list[str]:
    """The upload, then each download, oldest first, as the panel shows them."""
    item = history.item
    upload = f"⬆ Uploaded from {item.source_device} · {local_time(item.created_at)}"
    if item.to_device:
        upload += f" → {item.to_device}"
    lines = [f"History · {item.name}", upload]
    for download in history.downloads:
        lines.append(f"⬇ {download.device} → {download.destination} · {local_time(download.at)}")
    return lines


class SettingsDialog(QDialog):
    """Server address, token and device name."""

    def __init__(self, current: ClientConfig | None, default_device: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("spider settings")
        self.setMinimumWidth(420)
        self.server = QLineEdit(current.server if current else "")
        self.server.setPlaceholderText("http://100.95.121.2:8181")
        self.token = QLineEdit(current.token if current else "")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.device = QLineEdit(current.device if current else default_device)
        self.problem = QLabel("")
        self.problem.setStyleSheet("color: #d6455d;")

        form = QFormLayout()
        form.addRow("Server", self.server)
        form.addRow("Token", self.token)
        form.addRow("This device", self.device)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        # Strip first: these values are pasted, and a trailing newline would
        # become part of the token and never match the server's.
        server, token, device = (
            self.server.text().strip(),
            self.token.text().strip(),
            self.device.text().strip(),
        )
        if not server.startswith(("http://", "https://")):
            self.problem.setText("The server address must start with http:// or https://")
        elif len(token) < MIN_TOKEN_LENGTH:
            self.problem.setText(
                f"The token is {len(token)} characters; it must be at least {MIN_TOKEN_LENGTH}."
            )
        elif not device:
            self.problem.setText("Give this device a name.")
        else:
            self.result_config = ClientConfig(server=server.rstrip("/"), token=token, device=device)
            self.accept()


class MainWindow(QMainWindow):
    def __init__(
        self,
        make_client: Callable[[], SpiderClient],
        open_settings: Callable[[QWidget], bool],
    ) -> None:
        super().__init__()
        self._make_client = make_client
        self._open_settings = open_settings
        self._pool = QThreadPool.globalInstance()
        self._refresh_serial = 0
        # One poll at a time: it acks what it fetches, so two at once would race for
        # the same inbox. A refresh during a poll asks for one more poll afterwards.
        self._polling = False
        self._poll_again = False
        self._history_serial = 0
        self._busy = 0
        self._quitting = False
        self._items: list[Item] = []
        self._devices: list[Device] = []
        self._server_ok = True
        # True once a poll found the server predates devices: the sidebar and the
        # history panel stay hidden and their routes are not asked again.
        self._old_server = False
        self._thumbs: dict[str, QIcon] = {}
        # Every id ever asked for, so a missing thumbnail (None) is not fetched again.
        self._thumbs_asked: set[str] = set()
        self._type_icons: dict[str, QIcon] = {}
        # Notices not yet received or dismissed, oldest first. The poll acked them
        # on the server, so the banner and the tray menu are the only way back.
        self._notice_rows: dict[int, NoticeRow] = {}
        self._notice_serial = 0
        # Notices being received now: the banner, the tray entry and the notification
        # can each ask again mid-download, which would save a second copy.
        self._receiving: set[int] = set()
        self._last_notice: actions.Notice | None = None
        self._receive_actions: list[QAction] = []

        self.setWindowTitle("spider")
        self.setWindowIcon(make_icon())
        self.resize(1080, 640)
        self.setAcceptDrops(True)

        self.send_clipboard_button = QPushButton("Send clipboard")
        self.paste_button = QPushButton("Paste latest")
        self.files_button = QPushButton("Send files…")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)

        top = QHBoxLayout()
        for widget in (self.send_clipboard_button, self.paste_button, self.files_button):
            top.addWidget(widget)
        top.addWidget(self.search, 1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_PX)
        self.table.setIconSize(QSize(PREVIEW_PX, PREVIEW_PX))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, PREVIEW_PX + 12)
        self.table.setColumnWidth(1, 300)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.cellDoubleClicked.connect(lambda row, _col: self._open_row(row))
        self.table.itemSelectionChanged.connect(self._selection_changed)
        # Scrolling or resizing brings new rows into view, which may need thumbnails.
        self.table.verticalScrollBar().valueChanged.connect(self._fetch_thumbnails)
        self.table.verticalScrollBar().rangeChanged.connect(self._fetch_thumbnails)

        self.devices = QTreeWidget()
        self.devices.setColumnCount(2)
        self.devices.setHeaderHidden(True)
        self.devices.setRootIsDecorated(False)
        self.devices.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.devices.customContextMenuRequested.connect(self._show_device_menu)

        self.history = QListWidget()
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.history.setWordWrap(True)
        copy_lines = QAction("Copy", self.history)
        copy_lines.setShortcut(QKeySequence.StandardKey.Copy)
        copy_lines.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        copy_lines.triggered.connect(self._copy_history)
        self.history.addAction(copy_lines)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self._show_history_hint()

        self.empty = QLabel("Nothing here yet. Send the clipboard, or drop files on this window.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        listing = QWidget()
        listing_layout = QVBoxLayout(listing)
        listing_layout.setContentsMargins(0, 0, 0, 0)
        # The banner is the fallback for notification clicks, which macOS and some
        # Linux desktops never deliver to messageClicked.
        self.banner = QWidget()
        self.banner.setStyleSheet("NoticeRow { background: palette(alternate-base); }")
        self._banner_layout = QVBoxLayout(self.banner)
        self._banner_layout.setContentsMargins(0, 0, 0, 0)
        self._banner_layout.setSpacing(2)
        self.banner.setVisible(False)
        listing_layout.addWidget(self.banner)
        listing_layout.addWidget(self.table, 1)
        listing_layout.addWidget(self.empty, 1)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(listing)
        right.addWidget(self.history)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(self.devices)
        body.addWidget(right)
        body.setStretchFactor(1, 1)
        body.setSizes([220, 860])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(body, 1)
        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu("&File")
        settings = QAction("&Settings…", self)
        settings.triggered.connect(self._edit_settings)
        refresh = QAction("&Refresh", self)
        refresh.setShortcut("F5")
        refresh.triggered.connect(self.refresh)
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.quit_for_real)
        file_menu.addActions([settings, refresh])
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        # Lambdas: clicked passes `checked`, which must not land in `to_device`.
        self.send_clipboard_button.clicked.connect(lambda: self.send_clipboard())
        self.paste_button.clicked.connect(self.paste_latest)
        self.files_button.clicked.connect(lambda: self.choose_files())

        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())

        self._poll = QTimer(self, interval=REFRESH_MS)
        self._poll.timeout.connect(self.refresh)
        self._poll.start()

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()

        # The count sits apart from the message area, so a refresh after a send
        # cannot wipe the note that send just left there.
        self.count = QLabel("")
        self.statusBar().addPermanentWidget(self.count)
        self.statusBar().showMessage("Ready")
        self._set_empty(True)
        self._render_devices()

    # --- plumbing -----------------------------------------------------

    @Slot(object)
    def _invoke(self, call: Callable[[], None]) -> None:
        """Run a callback that a worker thread handed over, on the UI thread."""
        call()

    def _start(
        self, work: Callable, on_done: Callable, on_failed: Callable, priority: int = 0
    ) -> None:
        task = Task(self._make_client, work, on_done, on_failed)
        task.signals.call.connect(self._invoke)
        self._pool.start(task, priority)

    def run_task(self, work: Callable, on_done: Callable | None, label: str) -> None:
        self._busy += 1
        self.statusBar().showMessage(label)

        def succeeded(result) -> None:
            self._busy = max(0, self._busy - 1)
            if on_done is not None:
                on_done(result)

        def failed(message: str) -> None:
            self._busy = max(0, self._busy - 1)
            self.statusBar().showMessage(f"Error: {message}", 15_000)
            self.notify("spider", message)

        self._start(work, succeeded, failed)

    def notify(self, title: str, message: str) -> None:
        if self.tray is not None and not self.isVisible():
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _set_empty(self, empty: bool) -> None:
        self.empty.setVisible(empty)
        self.table.setVisible(not empty)

    # --- listing ------------------------------------------------------

    def refresh(self) -> None:
        # A slow response for an older search must not overwrite a newer one.
        self._refresh_serial += 1
        serial = self._refresh_serial
        query = self.search.text().strip()
        self._start(
            lambda client: actions.list_items(client, query),
            lambda items: self._show_items(serial, items),
            lambda message: self.statusBar().showMessage(f"Error: {message}", 15_000),
        )
        self._start_poll()

    def _start_poll(self) -> None:
        if self._polling:
            self._poll_again = True
            return
        if self._quitting:
            return  # a heartbeat now would undo the one quitting just cleared
        self._polling = True
        # The listing already reports an unreachable server in the status bar, so
        # a failed poll only turns the server light red.
        self._start(actions.poll, self._polled, lambda _message: self._poll_failed())

    def _poll_finished(self) -> None:
        self._polling = False
        if self._poll_again:
            self._poll_again = False
            self._start_poll()

    def _polled(self, result: actions.Poll) -> None:
        """Polls never overlap, so each result is the newest; and its notices are
        acked on the server already, so they are shown whatever else has happened."""
        try:
            self._server_ok = True
            self.show_devices(result.devices)
            self._on_notices(result.notices)
        finally:
            self._poll_finished()  # a rendering error must not stop polling for good

    def _poll_failed(self) -> None:
        self._server_ok = False
        self._render_devices()
        self._poll_finished()

    def _on_notices(self, notices: list[actions.Notice]) -> None:
        """Items sent to this device. The poll has already acked them, so each
        must be shown now or it is never offered again. One already pending is not
        shown twice."""
        pending = {i for row in self._notice_rows.values() for i in row.notice.item_ids}
        for notice in notices:
            if not set(notice.item_ids) <= pending:
                self.show_notice(notice)
                pending.update(notice.item_ids)

    def _show_items(self, serial: int, items: list[Item]) -> None:
        if serial != self._refresh_serial:
            return
        selected = self._selected_item()
        self._items = items
        # Rebuilding clears the selection for a moment; without blocking, the
        # history panel would flash its hint and reload twice every refresh.
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                source = item.source_device
                if item.to_device:
                    source = f"{source} → {item.to_device}"
                cells = (
                    "",
                    item.name,
                    item.kind.value,
                    humanize_size(item.size),
                    source,
                    humanize_age(item.created_at),
                    item.id[:SHORT_ID_LENGTH],
                )
                for column, text in enumerate(cells):
                    cell = QTableWidgetItem(text)
                    if column == 1 and item.preview:
                        cell.setToolTip(item.preview)
                    self.table.setItem(row, column, cell)
                self.table.item(row, 0).setIcon(self._preview_icon(item))
                if selected is not None and item.id == selected.id:
                    self.table.selectRow(row)
        finally:
            self.table.blockSignals(False)
        self._selection_changed()
        self._set_empty(not items)
        self._fetch_thumbnails()
        self.count.setText(f"{len(items)} item(s)")

    # --- previews -----------------------------------------------------

    def _preview_icon(self, item: Item) -> QIcon:
        """The thumbnail once it has arrived, the file-type logo until then (or for good)."""
        if item.id in self._thumbs:
            return self._thumbs[item.id]
        stem = actions.icon_name(item.name, item.content_type, item.kind)
        if stem not in self._type_icons:
            self._type_icons[stem] = QIcon(str(actions.ICON_DIR / f"{stem}.svg"))
        return self._type_icons[stem]

    def _fetch_thumbnails(self, *_args) -> None:
        """Ask for thumbnails of the image rows in view that were never asked for."""
        if self._old_server or not self._items:
            return
        first = max(self.table.rowAt(0), 0)
        last = self.table.rowAt(self.table.viewport().height() - 1)
        if last < 0:
            last = len(self._items) - 1
        for item in self._items[first : last + 1]:
            wanted = item.kind is ItemKind.file and item.content_type.startswith("image/")
            if not wanted or item.id in self._thumbs_asked:
                continue
            self._fetch_thumbnail(item.id)

    def _fetch_thumbnail(self, item_id: str) -> None:
        # Its own method so each callback closes over its own id, not the loop's last.
        self._thumbs_asked.add(item_id)
        # Low priority: a page of thumbnails must not queue ahead of a send.
        self._start(
            lambda client: client.thumbnail(item_id),
            lambda data: self._thumbnail_arrived(item_id, data),
            lambda _message: self._thumbs_asked.discard(item_id),
            priority=-1,
        )

    def _thumbnail_arrived(self, item_id: str, data: bytes | None) -> None:
        if data is None:
            return  # no thumbnail for this item; the logo stays, and we do not ask again
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._thumbs[item_id] = QIcon(pixmap)
        for row, item in enumerate(self._items):
            if item.id == item_id and self.table.item(row, 0) is not None:
                self.table.item(row, 0).setIcon(self._thumbs[item_id])

    # --- devices ------------------------------------------------------

    def show_devices(self, devices: list[Device] | None) -> None:
        """None means the server predates devices: hide what it cannot fill."""
        self._old_server = devices is None
        self.devices.setVisible(not self._old_server)
        self.history.setVisible(not self._old_server)
        self._devices = devices or []
        self._render_devices()

    def _identity(self) -> tuple[str, str]:
        """The server host and this device's name, read from the current settings."""
        client = self._make_client()
        try:
            config = client.config
        finally:
            client.close()
        return urlparse(config.server).hostname or config.server, config.device

    def _render_devices(self) -> None:
        host, this_device = self._identity()
        self.devices.clear()
        server = QTreeWidgetItem(self.devices, ["Server"])
        light = QTreeWidgetItem(server, [host, ""])
        light.setIcon(0, dot_icon(GREEN if self._server_ok else RED))
        light.setToolTip(0, "Reachable" if self._server_ok else "Cannot reach the server")
        running = QTreeWidgetItem(self.devices, ["Running"])
        offline = QTreeWidgetItem(self.devices, ["Offline"])
        for device in self._devices:
            label = device.name
            if device.name == this_device:
                label += " (this device)"
            if device.online:
                row = QTreeWidgetItem(running, [label, ""])
                row.setIcon(0, dot_icon(GREEN))
            else:
                row = QTreeWidgetItem(offline, [label, humanize_age(device.last_seen)])
                row.setIcon(0, dot_icon(GREY))
            row.setData(0, Qt.ItemDataRole.UserRole, device.name)
        for group in (server, running, offline):
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            group.setExpanded(True)
        self.devices.resizeColumnToContents(1)

    def _device_at(self, position: QPoint) -> str | None:
        """The other device under a point in the sidebar's viewport, if any."""
        row = self.devices.itemAt(position)
        name = row.data(0, Qt.ItemDataRole.UserRole) if row is not None else None
        if name is None or name == self._identity()[1]:
            return None  # sending to yourself is refused; a drop there is a plain send
        return name

    def _is_online(self, name: str) -> bool:
        return any(device.name == name and device.online for device in self._devices)

    def _show_device_menu(self, position) -> None:
        name = self._device_at(position)
        if name is None:
            return
        menu = QMenu(self)
        menu.addAction("Send clipboard to this device", lambda: self.send_clipboard(name))
        menu.addAction("Send files to this device…", lambda: self.choose_files(name))
        if not self._is_online(name):
            menu.addSeparator()
            menu.addAction("Remove from list", lambda: self.remove_device(name))
        menu.exec(self.devices.viewport().mapToGlobal(position))

    def remove_device(self, name: str) -> None:
        self.run_task(
            lambda client: client.remove_device(name),
            lambda _result: self._sent(f"Removed {name} from the list"),
            f"Removing {name}…",
        )

    # --- history ------------------------------------------------------

    def _selection_changed(self) -> None:
        item = self._selected_item()
        if item is None:
            self._history_serial += 1  # drop any answer still on its way
            self._show_history_hint()
        else:
            self._load_history(item.id)

    def _show_history_hint(self) -> None:
        self.history.clear()
        hint = QListWidgetItem(NO_SELECTION)
        hint.setFlags(Qt.ItemFlag.NoItemFlags)
        self.history.addItem(hint)

    def _load_history(self, item_id: str) -> None:
        # Moving quickly between rows must not leave an older row's history showing.
        self._history_serial += 1
        serial = self._history_serial
        if self._old_server:
            return
        self._start(
            lambda client: actions.load_history(client, item_id),
            lambda history: self._show_history(serial, history),
            lambda message: self._history_failed(serial, message),
        )

    def _show_history(self, serial: int, history: ItemHistory | None) -> None:
        if serial != self._history_serial:
            return
        if history is None:
            self.history.setVisible(False)
            return
        self.history.clear()
        self.history.addItems(history_lines(history))

    def _history_failed(self, serial: int, message: str) -> None:
        if serial != self._history_serial:
            return
        self.history.clear()
        self.history.addItem(f"Could not load the history: {message}")

    def _copy_history(self) -> None:
        lines = [row.text() for row in self.history.selectedItems()]
        if lines:
            QGuiApplication.clipboard().setText("\n".join(lines))

    def _selected_item(self) -> Item | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._items):
            return None
        return self._items[rows[0].row()]

    # --- actions ------------------------------------------------------

    def send_clipboard(self, to_device: str | None = None) -> None:
        text = QGuiApplication.clipboard().text()
        self.run_task(
            lambda client: actions.send_text(client, text, to_device),
            lambda item: self._sent(
                f"Sent {humanize_size(item.size)} as “{item.name}”{self._to(to_device)}",
                to_device,
            ),
            "Sending clipboard…",
        )

    def paste_latest(self) -> None:
        self._paste("latest")

    def _paste(self, ref: str) -> None:
        self.run_task(
            lambda client: actions.fetch_text(client, ref),
            self._copy_to_clipboard,
            "Fetching text…",
        )

    def _copy_to_clipboard(self, result: tuple[Item, str]) -> None:
        item, text = result
        QGuiApplication.clipboard().setText(text)
        message = f"Copied “{item.name}” ({humanize_size(item.size)}) to the clipboard"
        self.statusBar().showMessage(message, 8000)
        self.notify("spider", message)

    def choose_files(self, to_device: str | None = None) -> None:
        title = f"Send files to {to_device}" if to_device else "Send files"
        names, _ = QFileDialog.getOpenFileNames(self, title)
        if names:
            self.send_paths([Path(name) for name in names], to_device)

    def send_paths(self, paths: list[Path], to_device: str | None = None) -> None:
        self.run_task(
            lambda client: actions.send_files(client, paths, to_device),
            lambda items: self._sent(f"Sent {len(items)} file(s){self._to(to_device)}", to_device),
            f"Sending {len(paths)} file(s)…",
        )

    @staticmethod
    def _to(to_device: str | None) -> str:
        return f" to {to_device}" if to_device else ""

    def _sent(self, message: str, to_device: str | None = None) -> None:
        self.notify("spider", message)
        if to_device and not self._is_online(to_device):
            # Still sent: the item waits in that device's inbox for its next desktop app.
            message = f"{to_device} has no desktop app running; it will see this when it opens one."
        self.statusBar().showMessage(message, 8000)
        self.refresh()

    def _open_row(self, row: int) -> None:
        if row >= len(self._items):
            return
        item = self._items[row]
        if item.kind is ItemKind.text:
            self._paste(item.id)
        else:
            self.save_item(item)

    def save_item(self, item: Item) -> None:
        start = str(Path.home() / "Downloads" / actions.suggested_filename(item))
        name, _ = QFileDialog.getSaveFileName(self, "Save as", start)
        if not name:
            return
        target = Path(name)
        self.run_task(
            lambda client: actions.save_item(client, item.id, target),
            lambda result: self.statusBar().showMessage(
                f"Saved {target} ({humanize_size(result[0].size)}, sha256 verified)"
                + (f" — {result[1]}" if result[1] else ""),
                10_000,
            ),
            f"Downloading {item.name}…",
        )

    def delete_item(self, item: Item) -> None:
        answer = QMessageBox.question(
            self,
            "Delete",
            f"Delete “{item.name}” from the server?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.run_task(
            lambda client: actions.delete_item(client, item.id),
            lambda _result: self._sent(f"Deleted “{item.name}”"),
            "Deleting…",
        )

    def _show_menu(self, position) -> None:
        item = self._item_at(position)
        if item is None:
            return
        menu = QMenu(self)
        if item.kind is ItemKind.text:
            menu.addAction("Copy to clipboard", lambda: self._paste(item.id))
        menu.addAction("Save as…", lambda: self.save_item(item))
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.delete_item(item))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _item_at(self, position) -> Item | None:
        row = self.table.rowAt(position.y())
        if row < 0 or row >= len(self._items):
            return None
        self.table.selectRow(row)
        return self._items[row]

    # --- drag and drop ------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop_target(self, event) -> str | None:
        """The device row under a drag, in window coordinates; None for anywhere else."""
        if self.devices.isHidden():
            return None
        point = self.devices.viewport().mapFrom(self, event.position().toPoint())
        if not self.devices.viewport().rect().contains(point):
            return None
        return self._device_at(point)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.send_paths(paths, self._drop_target(event))

    # --- receiving ----------------------------------------------------

    def show_notice(self, notice: actions.Notice) -> None:
        """Unlike `notify`, shown even with the window open: something arrived for us."""
        self._last_notice = notice
        if self.tray is not None:
            self.tray.showMessage(
                notice.title, notice.message, QSystemTrayIcon.MessageIcon.Information, 10_000
            )
        self._notice_serial += 1
        key = self._notice_serial
        row = NoticeRow(notice, self.banner)
        row.action.clicked.connect(lambda: self.receive_notice(notice))
        row.dismiss.clicked.connect(lambda: self._drop_notice(key))
        self._notice_rows[key] = row
        self._banner_layout.addWidget(row)
        self.banner.setVisible(True)
        self._render_receive_actions()

    def _notice_key(self, notice: actions.Notice) -> int | None:
        for key, row in self._notice_rows.items():
            if row.notice is notice:
                return key
        return None

    def _drop_notice(self, key: int) -> None:
        row = self._notice_rows.pop(key, None)
        if row is not None:
            self._banner_layout.removeWidget(row)
            row.deleteLater()
        self.banner.setVisible(bool(self._notice_rows))
        self._render_receive_actions()

    def _notice_label(self, notice: actions.Notice) -> str:
        """The first item's name, from the listing or the notice's own copy."""
        names = {item.id: item.name for item in self._items}
        if notice.item_ids and notice.item_ids[0] in names:
            return names[notice.item_ids[0]]
        return notice_name(notice) or notice.message.partition(" — ")[0]

    def _render_receive_actions(self) -> None:
        """The newest few pending notices as "Receive …" entries atop the tray menu."""
        menu = self.tray.contextMenu() if self.tray is not None else None
        for action in self._receive_actions:
            if menu is not None:
                menu.removeAction(action)
            action.deleteLater()
        self._receive_actions = []
        newest = list(self._notice_rows.values())[-TRAY_RECEIVE_LIMIT:][::-1]
        for row in newest:
            notice = row.notice
            action = QAction(f"Receive {self._notice_label(notice)}", self)
            action.triggered.connect(lambda _checked=False, n=notice: self.receive_notice(n))
            self._receive_actions.append(action)
        if menu is not None and self._receive_actions:
            first = menu.actions()[0] if menu.actions() else None
            menu.insertActions(first, self._receive_actions)

    def _notice_clicked(self) -> None:
        notice = self._last_notice
        # A notice already received or dismissed must not fetch its items again.
        if notice is not None and self._notice_key(notice) is not None:
            self.receive_notice(notice)

    def receive_notice(self, notice: actions.Notice) -> None:
        """Take every item a notice names: files into Downloads, text onto the clipboard."""
        key = self._notice_key(notice)
        if key is not None:
            if key in self._receiving:
                return
            self._receiving.add(key)
            self._notice_rows[key].action.setEnabled(False)
        item_ids = list(notice.item_ids)
        fallback = notice_name(notice) or "That item"

        def work(client: SpiderClient) -> list[actions.Received | str]:
            # Read at click time: the tests point it elsewhere.
            downloads = actions.downloads_dir()
            outcomes: list[actions.Received | str] = []
            for item_id in item_ids:
                try:
                    outcomes.append(actions.receive(client, item_id, downloads))
                except SpiderError as exc:
                    if exc.code is not ErrorCode.not_found:
                        raise
                    outcomes.append(item_id)  # deleted since it was sent
            return outcomes

        def failed(message: str) -> None:
            self._busy = max(0, self._busy - 1)
            self._receiving.discard(key)
            if key in self._notice_rows:
                self._notice_rows[key].action.setEnabled(True)  # let them try again
            self.statusBar().showMessage(f"Error: {message}", 15_000)
            self.notify("spider", message)

        def done(outcomes: list[actions.Received | str]) -> None:
            self._busy = max(0, self._busy - 1)
            self._receiving.discard(key)
            if key is not None:
                self._drop_notice(key)
            self._received(outcomes, fallback)

        self._busy += 1
        self.statusBar().showMessage("Receiving…")
        self._start(work, done, failed)

    def _received(self, outcomes: list[actions.Received | str], fallback: str) -> None:
        names = {item.id: item.name for item in self._items}
        saved = [o.path for o in outcomes if isinstance(o, actions.Received) and o.path]
        messages: list[str] = []
        if len(saved) == 1:
            messages.append(f"Saved to {saved[0]}")
        elif saved:
            messages.append(f"Saved {len(saved)} files to {saved[0].parent}")
        for outcome in outcomes:
            if isinstance(outcome, str):
                messages.append(f"“{names.get(outcome, fallback)}” is no longer on the server")
            elif outcome.text is not None:
                QGuiApplication.clipboard().setText(outcome.text)
                messages.append(f"Copied “{outcome.item.name}” to the clipboard")
        for message in messages:
            self.notify("spider", message)
        warnings = [o.warning for o in outcomes if isinstance(o, actions.Received) and o.warning]
        if warnings:
            messages.append(f"Saved, but history was not updated: {warnings[0]}")
        if messages:
            self.statusBar().showMessage(messages[-1], 10_000)
        self.refresh()

    # --- settings, tray, closing -------------------------------------

    def _edit_settings(self) -> None:
        if self._open_settings(self):
            self.refresh()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        menu.addAction("Send clipboard", lambda: self.send_clipboard())
        menu.addAction("Paste latest", self.paste_latest)
        menu.addSeparator()
        menu.addAction("Show spider", self.show_window)
        menu.addAction("Quit", self.quit_for_real)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("spider")
        self.tray.activated.connect(self._tray_clicked)
        self.tray.messageClicked.connect(self._notice_clicked)
        self.tray.show()

    def _tray_clicked(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def quit_for_real(self) -> None:
        self._quitting = True
        self._poll.stop()
        # Turn grey on the other machines now rather than in 30 seconds. Fired and
        # forgotten: an unreachable server must not hold up quitting.
        self._start(lambda client: client.stop_heartbeat(), lambda _r: None, lambda _m: None)
        self.close()
        QGuiApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # With a tray icon, closing the window keeps spider running in the tray.
        if self.tray is not None and not self._quitting:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "spider", "Still running in the tray.", QSystemTrayIcon.MessageIcon.Information
            )
            return
        event.accept()
