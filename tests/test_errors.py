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


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _all_imports(package: str) -> set[str]:
    names: set[str] = set()
    for path in (SRC / package).rglob("*.py"):
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
    allowed_prefixes = ("spider.", "pydantic")
    stdlib_ok = {
        "os", "time", "enum", "datetime", "pathlib", "tomllib", "typing",
        "dataclasses", "sys", "shutil", "socket", "stat", "collections",
        "threading", "__future__",
    }
    offenders = set()
    for name in _all_imports("core"):
        root = name.split(".")[0]
        if root in stdlib_ok or name.startswith(allowed_prefixes):
            continue
        offenders.add(name)
    assert offenders == set()
