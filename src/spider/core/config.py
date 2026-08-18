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


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def save_client_config(config: ClientConfig, path: Path | None = None) -> Path:
    path = client_config_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f'server = "{_toml_escape(config.server)}"\n'
        f'token = "{_toml_escape(config.token)}"\n'
        f'device = "{_toml_escape(config.device)}"\n'
    )
    # Create with 0600 from the start so the token is never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
    """True when anyone other than the owner can read the file holding the token."""
    if os.name == "nt" or not path.exists():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return bool(mode & 0o077)
