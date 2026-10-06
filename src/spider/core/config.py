"""Configuration for both sides.

The server reads environment variables. The CLI reads a TOML file that the
`spider init` command writes. Both live here so the variable names have one
definition.
"""

from __future__ import annotations

import os
import socket
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from spider.core.errors import ErrorCode, SpiderError

MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path
    token: str
    min_free_gb: float = 10.0
    max_item_mb: int = 0
    retention_days: int | None = None


def load_server_config(env: Mapping[str, str] | None = None) -> ServerConfig:
    """Build the server configuration, refusing to run without a real token.

    Raising here rather than defaulting is deliberate: a server that silently
    starts without authentication is worse than one that does not start.
    """
    env = os.environ if env is None else env
    token = env.get("SPIDER_TOKEN", "")
    if not token:
        raise RuntimeError("SPIDER_TOKEN is not set. Refusing to start without a token.")
    if len(token) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"SPIDER_TOKEN is {len(token)} characters. "
            f"It must be at least {MIN_TOKEN_LENGTH}. Refusing to start."
        )
    retention_raw = env.get("SPIDER_RETENTION_DAYS", "").strip()
    return ServerConfig(
        data_dir=Path(env.get("SPIDER_DATA_DIR", "/data")),
        token=token,
        min_free_gb=float(env.get("SPIDER_MIN_FREE_GB", "10")),
        max_item_mb=int(env.get("SPIDER_MAX_ITEM_MB", "0")),
        retention_days=int(retention_raw) if retention_raw else None,
    )


@dataclass(frozen=True)
class ClientConfig:
    server: str
    token: str
    device: str


def default_device_name() -> str:
    return socket.gethostname().split(".")[0] or "unknown"


def client_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "spider" / "config.toml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "spider" / "config.toml"


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _toml_escape(value: str) -> str:
    """Escape a string so it is a valid TOML basic string.

    TOML forbids raw control characters inside a basic string. Escaping only
    backslash and quote leaves a pasted trailing newline in the file verbatim,
    and `tomllib` then refuses to parse the config back.
    """
    out: list[str] = []
    for char in value:
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return "".join(out)


def save_client_config(config: ClientConfig, path: Path | None = None) -> Path:
    path = client_config_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f'server = "{_toml_escape(config.server)}"\n'
        f'token = "{_toml_escape(config.token)}"\n'
        f'device = "{_toml_escape(config.device)}"\n'
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        # The mode passed to os.open applies only when it creates the file, so
        # an existing config left at 0644 would hold the new token at that wider
        # mode. Narrow the open descriptor before any bytes are written; the
        # write is buffered, so nothing has reached the disk yet.
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o600)
        handle.write(body)
    path.chmod(0o600)
    return path


def load_client_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> ClientConfig:
    path = client_config_path() if path is None else path
    env = os.environ if env is None else env
    if not path.exists():
        raise SpiderError(
            ErrorCode.bad_request,
            f"No configuration at {path}. Run `spider init` first.",
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ClientConfig(
        server=str(env.get("SPIDER_SERVER") or data.get("server", "")).rstrip("/"),
        token=str(env.get("SPIDER_TOKEN") or data.get("token", "")),
        device=str(env.get("SPIDER_DEVICE") or data.get("device", default_device_name())),
    )


def has_insecure_permissions(path: Path) -> bool:
    """True when the file holding the token is reachable by anyone but its owner.

    The mask is deliberately wider than "readable": a group-writable config is
    a way to acquire the token too. It over-reports rather than under-reports.
    """
    if os.name == "nt" or not path.exists():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return bool(mode & 0o077)
