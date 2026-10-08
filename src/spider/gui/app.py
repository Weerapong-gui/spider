"""Entry point for the `spider-gui` command."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from spider.cli.client import SpiderClient
from spider.core.config import (
    ClientConfig,
    client_config_path,
    default_device_name,
    load_client_config,
    save_client_config,
)
from spider.core.errors import SpiderError
from spider.gui.window import MainWindow, SettingsDialog, make_icon


class Session:
    """Holds the current config so Settings can change it while the app runs."""

    def __init__(self, config: ClientConfig | None) -> None:
        self.config = config

    def make_client(self) -> SpiderClient:
        # main() does not open the window until a config exists.
        assert self.config is not None
        return SpiderClient(self.config)

    def edit(self, parent: QWidget | None) -> bool:
        dialog = SettingsDialog(self.config, default_device_name(), parent)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return False
        self.config = dialog.result_config
        save_client_config(self.config, client_config_path())
        return True


def load_existing() -> ClientConfig | None:
    try:
        return load_client_config(client_config_path())
    except (SpiderError, OSError, ValueError):
        return None


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("spider")
    app.setWindowIcon(make_icon())
    # With a tray icon, closing the window hides it and spider keeps running.
    # Without one there would be no way back to a hidden window, so quit instead.
    app.setQuitOnLastWindowClosed(not QSystemTrayIcon.isSystemTrayAvailable())

    session = Session(load_existing())
    if session.config is None and not session.edit(None):
        return 0

    window = MainWindow(session.make_client, session.edit)
    window.show()
    window.refresh()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
