"""The main window, the settings dialog, and the tray icon."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spider.cli.client import SpiderClient
from spider.cli.main import SHORT_ID_LENGTH, humanize_age, humanize_size
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig
from spider.core.models import Item, ItemKind
from spider.gui import actions
from spider.gui.tasks import Task

REFRESH_MS = 10_000
COLUMNS = ("Name", "Kind", "Size", "From", "When", "ID")


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
        self._busy = 0
        self._quitting = False
        self._items: list[Item] = []

        self.setWindowTitle("spider")
        self.setWindowIcon(make_icon())
        self.resize(860, 520)
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
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.cellDoubleClicked.connect(lambda row, _col: self._open_row(row))

        self.empty = QLabel("Nothing here yet. Send the clipboard, or drop files on this window.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty)
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

        self.send_clipboard_button.clicked.connect(self.send_clipboard)
        self.paste_button.clicked.connect(self.paste_latest)
        self.files_button.clicked.connect(self.choose_files)

        self._search_timer = QTimer(self, singleShot=True, interval=250)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())

        self._poll = QTimer(self, interval=REFRESH_MS)
        self._poll.timeout.connect(self.refresh)
        self._poll.start()

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()

        self.statusBar().showMessage("Ready")
        self._set_empty(True)

    # --- plumbing -----------------------------------------------------

    @Slot(object)
    def _invoke(self, call: Callable[[], None]) -> None:
        """Run a callback that a worker thread handed over, on the UI thread."""
        call()

    def _start(self, work: Callable, on_done: Callable, on_failed: Callable) -> None:
        task = Task(self._make_client, work, on_done, on_failed)
        task.signals.call.connect(self._invoke)
        self._pool.start(task)

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

    def _show_items(self, serial: int, items: list[Item]) -> None:
        if serial != self._refresh_serial:
            return
        selected = self._selected_item()
        self._items = items
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            cells = (
                item.name,
                item.kind.value,
                humanize_size(item.size),
                item.source_device,
                humanize_age(item.created_at),
                item.id[:SHORT_ID_LENGTH],
            )
            for column, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if column == 0 and item.preview:
                    cell.setToolTip(item.preview)
                self.table.setItem(row, column, cell)
            if selected is not None and item.id == selected.id:
                self.table.selectRow(row)
        self._set_empty(not items)
        if not self._busy:
            self.statusBar().showMessage(f"{len(items)} item(s)")

    def _selected_item(self) -> Item | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._items):
            return None
        return self._items[rows[0].row()]

    # --- actions ------------------------------------------------------

    def send_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text()
        self.run_task(
            lambda client: actions.send_text(client, text),
            lambda item: self._sent(f"Sent {humanize_size(item.size)} as “{item.name}”"),
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

    def choose_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Send files")
        if names:
            self.send_paths([Path(name) for name in names])

    def send_paths(self, paths: list[Path]) -> None:
        self.run_task(
            lambda client: actions.send_files(client, paths),
            lambda items: self._sent(f"Sent {len(items)} file(s)"),
            f"Sending {len(paths)} file(s)…",
        )

    def _sent(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.notify("spider", message)
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
            lambda saved: self.statusBar().showMessage(
                f"Saved {target} ({humanize_size(saved.size)}, sha256 verified)", 10_000
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

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.send_paths(paths)

    # --- settings, tray, closing -------------------------------------

    def _edit_settings(self) -> None:
        if self._open_settings(self):
            self.refresh()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        menu.addAction("Send clipboard", self.send_clipboard)
        menu.addAction("Paste latest", self.paste_latest)
        menu.addSeparator()
        menu.addAction("Show spider", self.show_window)
        menu.addAction("Quit", self.quit_for_real)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("spider")
        self.tray.activated.connect(self._tray_clicked)
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
