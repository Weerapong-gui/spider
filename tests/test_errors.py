import ast
import pathlib

import pytest

from spider.core.errors import ErrorCode, SpiderError, error_payload

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "spider"


def test_payload_shape_is_exact():
    assert error_payload(ErrorCode.not_found, "no such item") == {
        "error": {"code": "not_found", "message": "no such item"}
    }


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.unauthorized, 401),
        (ErrorCode.bad_request, 400),
        (ErrorCode.not_found, 404),
        (ErrorCode.ambiguous_id, 409),
        (ErrorCode.checksum_mismatch, 422),
        (ErrorCode.disk_full, 507),
    ],
)
def test_http_status_mapping(code, status):
    assert SpiderError(code, "x").http_status == status


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [
        (ErrorCode.unauthorized, 3),
        (ErrorCode.not_found, 4),
        (ErrorCode.ambiguous_id, 5),
        (ErrorCode.checksum_mismatch, 6),
        (ErrorCode.disk_full, 7),
        (ErrorCode.unreachable, 8),
    ],
)
def test_exit_code_mapping(code, exit_code):
    assert SpiderError(code, "x").exit_code == exit_code


def test_spider_error_is_an_exception_with_a_readable_message():
    with pytest.raises(SpiderError, match="disk is full"):
        raise SpiderError(ErrorCode.disk_full, "disk is full")


def _resolve_imports(source: str, module_parts: tuple[str, ...]) -> set[str]:
    """Every dotted name a module imports, resolved to an absolute path.

    `module_parts` is the module's own dotted path, e.g. ("spider", "core",
    "errors"). Relative imports have to be resolved against it: `from ..server
    import storage` reaches ast as module="server", level=2, and the bare name
    "server" would never match a "spider.server" prefix test — the exact
    violation this guard exists to catch would pass unnoticed.

    `from X import y` may be importing a submodule named y, so the combined
    path is recorded too. Recording a name that turns out to be a class costs
    nothing: no class name can make an import look like it crosses a boundary
    when it does not.
    """
    tree = ast.parse(source)
    package = module_parts[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            anchor = (
                list(package[: max(0, len(package) - (node.level - 1))])
                if node.level
                else []
            )
            base = [*anchor, *(node.module.split(".") if node.module else [])]
            if base:
                names.add(".".join(base))
            names.update(".".join([*base, alias.name]) for alias in node.names)
    return names


def _imported_modules(path: pathlib.Path) -> set[str]:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return _resolve_imports(path.read_text(encoding="utf-8"), parts)


def _package_files(package: str) -> list[pathlib.Path]:
    return sorted((SRC / package).rglob("*.py"))


def _all_imports(package: str) -> set[str]:
    names: set[str] = set()
    for path in _package_files(package):
        names |= _imported_modules(path)
    return names


def test_core_never_imports_server_or_cli():
    offenders = {n for n in _all_imports("core") if n.startswith(("spider.server", "spider.cli"))}
    assert offenders == set()


def test_server_never_imports_cli():
    offenders = {n for n in _all_imports("server") if n.startswith("spider.cli")}
    assert offenders == set()


def test_cli_never_imports_server():
    offenders = {n for n in _all_imports("cli") if n.startswith("spider.server")}
    assert offenders == set()


def test_core_uses_no_third_party_dependency_except_pydantic():
    own = {"spider", "pydantic"}
    stdlib_ok = {
        "os", "time", "enum", "datetime", "pathlib", "tomllib", "typing",
        "dataclasses", "sys", "shutil", "socket", "stat", "collections",
        "threading", "__future__",
    }
    offenders = set()
    for name in _all_imports("core"):
        root = name.split(".")[0]
        if root in stdlib_ok or root in own:
            continue
        offenders.add(name)
    assert offenders == set()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import spider.server.storage", "spider.server.storage"),
        ("from spider.cli import client", "spider.cli.client"),
        ("from ..server import storage", "spider.server.storage"),
        ("from .. import server", "spider.server"),
        ("from . import models", "spider.core.models"),
    ],
    ids=["absolute", "absolute-from", "relative-deep", "relative-bare", "sibling"],
)
def test_relative_imports_resolve_to_absolute_names(source, expected):
    """Without resolution a relative import reads as a bare name and slips past
    every prefix test below."""
    assert expected in _resolve_imports(source, ("spider", "core", "errors"))


def test_the_guard_actually_scans_files():
    """A guard that walks zero files passes forever. `server` and `cli` do not
    exist yet; this asserts each one is scanned as soon as it does."""
    assert _package_files("core"), "core has no source files to scan"
    for package in ("server", "cli"):
        if (SRC / package).is_dir():
            assert _package_files(package), f"{package} exists but has no files to scan"
