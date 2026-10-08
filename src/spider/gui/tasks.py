"""Run blocking client calls off the UI thread.

A transfer can take minutes. If it ran on the UI thread the window would
freeze, so each call runs on Qt's thread pool and reports back by signal.
Every task builds its own `SpiderClient`: the object is cheap, and sharing one
across threads is not worth reasoning about.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from spider.cli.client import SpiderClient
from spider.core.errors import SpiderError


def describe_error(exc: BaseException) -> str:
    return exc.message if isinstance(exc, SpiderError) else f"{type(exc).__name__}: {exc}"


class _Signals(QObject):
    # Carries a zero-argument callable. The window connects this to one of its
    # own methods, so Qt delivers it on the UI thread. Connecting a bare lambda
    # instead would run it on the worker thread and touch widgets from there.
    call = Signal(object)


class Task(QRunnable):
    def __init__(
        self,
        make_client: Callable[[], SpiderClient],
        work: Callable,
        on_done: Callable[[object], None],
        on_failed: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._make_client = make_client
        self._work = work
        self._on_done = on_done
        self._on_failed = on_failed
        self.signals = _Signals()

    def run(self) -> None:
        try:
            client = self._make_client()
            try:
                result = self._work(client)
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 - every failure must reach the UI
            message = describe_error(exc)
            self.signals.call.emit(lambda: self._on_failed(message))
        else:
            self.signals.call.emit(lambda: self._on_done(result))
