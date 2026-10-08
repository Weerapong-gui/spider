"""Clipboard access, and a useful message when there is none.

Machines reached over SSH have no clipboard at all. That is a normal situation
here, not a crash, so every failure explains the `spider cat` route instead.
"""

from __future__ import annotations

import sys

import pyperclip

from spider.core.errors import ErrorCode, SpiderError

_FALLBACK = "Use `spider cat <id>` and `spider push -` on this machine instead."


def clipboard_hint() -> str:
    if sys.platform.startswith("linux"):
        return (
            "No clipboard backend found. Install one: `sudo apt install xclip` on X11, "
            f"or `sudo apt install wl-clipboard` on Wayland. {_FALLBACK}"
        )
    return f"No clipboard is available in this session. {_FALLBACK}"


def clipboard_available() -> bool:
    try:
        pyperclip.paste()
    except pyperclip.PyperclipException:
        return False
    return True


def read_clipboard() -> str:
    try:
        text = pyperclip.paste()
    except pyperclip.PyperclipException as exc:
        raise SpiderError(ErrorCode.bad_request, clipboard_hint()) from exc
    if not text or not text.strip():
        raise SpiderError(
            ErrorCode.bad_request,
            'The clipboard is empty. Copy something first, or use `spider push -t "..."`.',
        )
    return text


def write_clipboard(text: str) -> None:
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:
        raise SpiderError(ErrorCode.bad_request, clipboard_hint()) from exc
