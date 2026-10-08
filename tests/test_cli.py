import hashlib
import io
import tomllib

import httpx
import pyperclip
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from spider.cli import clipboard
from spider.cli import main as cli_main
from spider.cli.client import SpiderClient
from spider.core.config import MIN_TOKEN_LENGTH, ClientConfig
from spider.core.errors import ErrorCode, SpiderError
from spider.core.models import ItemKind
from spider.server.app import create_app
from spider.server.storage import Storage

TOKEN = "c" * MIN_TOKEN_LENGTH


def in_process(app) -> httpx.BaseTransport:
    """A synchronous transport that calls the real app directly.

    `httpx.ASGITransport` is async-only and cannot back the sync `httpx.Client`
    that SpiderClient uses, so borrow the sync bridge Starlette's TestClient
    already ships with.
    """
    return TestClient(app)._transport


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


@pytest.fixture
def config():
    return ClientConfig(server="http://spider.test", token=TOKEN, device="test-device")


@pytest.fixture
def api(store, config):
    """A SpiderClient wired straight into the real app, in this process.

    No mock server, no sockets, no stubbed responses. If the two sides ever
    disagree about a field name, these tests are where it shows up.
    """
    transport = in_process(create_app(store, TOKEN))
    client = SpiderClient(config, transport=transport)
    yield client
    client.close()


def test_push_bytes_returns_an_item(api):
    item = api.push_bytes(b"hello", kind=ItemKind.text, name="greeting")
    assert item.name == "greeting"
    assert item.size == 5
    assert item.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert item.source_device == "test-device"


def test_push_file_reads_from_disk(api, tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7 body")
    item = api.push_file(path)
    assert item.name == "report.pdf"
    assert item.content_type == "application/pdf"
    assert item.size == 13


def test_push_file_sends_a_checksum_the_server_agrees_with(api, tmp_path):
    path = tmp_path / "data.bin"
    payload = bytes(range(256))
    path.write_bytes(payload)
    item = api.push_file(path)
    assert item.sha256 == hashlib.sha256(payload).hexdigest()


def test_list_returns_newest_first(api):
    api.push_bytes(b"1", kind=ItemKind.text, name="one")
    api.push_bytes(b"2", kind=ItemKind.text, name="two")
    page = api.list_items()
    assert [item.name for item in page.items] == ["two", "one"]


def test_resolve_accepts_latest_and_prefixes(api):
    api.push_bytes(b"old", kind=ItemKind.text, name="old")
    newest = api.push_bytes(b"new", kind=ItemKind.text, name="new")
    assert api.resolve("latest").id == newest.id
    assert api.resolve(newest.id[:10]).id == newest.id


def test_download_writes_the_exact_bytes(api):
    payload = bytes(range(256))
    pushed = api.push_bytes(payload, kind=ItemKind.file, name="all.bin")
    sink = io.BytesIO()
    item = api.download(pushed.id, sink)
    assert sink.getvalue() == payload
    assert item.sha256 == pushed.sha256


def test_download_raises_on_a_corrupted_stream(api, store, monkeypatch):
    pushed = api.push_bytes(b"original bytes", kind=ItemKind.file, name="x.bin")
    store.blob_path(pushed.id).write_bytes(b"tampered bytes")
    with pytest.raises(SpiderError) as caught:
        api.download(pushed.id, io.BytesIO())
    assert caught.value.code is ErrorCode.checksum_mismatch


def test_delete_removes_the_item(api):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="x")
    api.delete(pushed.id)
    with pytest.raises(SpiderError) as caught:
        api.resolve(pushed.id)
    assert caught.value.code is ErrorCode.not_found


def test_verify_reports_missing_blobs(api, store):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="x")
    assert api.verify() == []
    store.blob_path(pushed.id).unlink()
    assert [item.id for item in api.verify()] == [pushed.id]


def test_a_wrong_token_surfaces_as_unauthorized(store, config):
    transport = in_process(create_app(store, TOKEN))
    wrong = SpiderClient(ClientConfig(config.server, "w" * 40, config.device), transport=transport)
    try:
        with pytest.raises(SpiderError) as caught:
            wrong.list_items()
        assert caught.value.code is ErrorCode.unauthorized
    finally:
        wrong.close()


def test_an_unreachable_server_surfaces_as_unreachable(config):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = SpiderClient(config, transport=httpx.MockTransport(refuse))
    try:
        with pytest.raises(SpiderError) as caught:
            client.list_items()
        assert caught.value.code is ErrorCode.unreachable
        assert caught.value.exit_code == 8
    finally:
        client.close()


def test_an_ambiguous_prefix_surfaces_with_its_code(api):
    first = api.push_bytes(b"a", kind=ItemKind.file, name="a")
    api.push_bytes(b"b", kind=ItemKind.file, name="b")
    with pytest.raises(SpiderError) as caught:
        api.resolve(first.id[:3])
    assert caught.value.code is ErrorCode.ambiguous_id
    assert caught.value.exit_code == 5


def test_read_returns_what_the_clipboard_holds(monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "สวัสดี from the clipboard")
    assert clipboard.read_clipboard() == "สวัสดี from the clipboard"


def test_read_rejects_an_empty_clipboard(monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "   ")
    with pytest.raises(SpiderError) as caught:
        clipboard.read_clipboard()
    assert caught.value.code is ErrorCode.bad_request
    assert "empty" in caught.value.message.lower()


def test_read_explains_a_missing_backend(monkeypatch):
    def unavailable():
        raise pyperclip.PyperclipException("no copy mechanism")

    monkeypatch.setattr(pyperclip, "paste", unavailable)
    with pytest.raises(SpiderError) as caught:
        clipboard.read_clipboard()
    assert caught.value.code is ErrorCode.bad_request
    assert "spider cat" in caught.value.message


def test_write_puts_text_on_the_clipboard(monkeypatch):
    written = []
    monkeypatch.setattr(pyperclip, "copy", written.append)
    clipboard.write_clipboard("hello")
    assert written == ["hello"]


def test_write_explains_a_missing_backend(monkeypatch):
    def unavailable(_text):
        raise pyperclip.PyperclipException("no copy mechanism")

    monkeypatch.setattr(pyperclip, "copy", unavailable)
    with pytest.raises(SpiderError) as caught:
        clipboard.write_clipboard("hello")
    assert "spider cat" in caught.value.message


def test_availability_probe(monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "")
    assert clipboard.clipboard_available() is True

    def unavailable():
        raise pyperclip.PyperclipException("nope")

    monkeypatch.setattr(pyperclip, "paste", unavailable)
    assert clipboard.clipboard_available() is False


def test_hint_is_not_empty():
    assert clipboard.clipboard_hint().strip() != ""


runner = CliRunner()


def combined(result) -> str:
    """Everything the command printed, on either stream.

    click 8.1 folds stderr into `result.output`; click 8.2 keeps them apart.
    Error messages go to stderr, so assertions about them read both.
    """
    try:
        return result.output + (result.stderr or "")
    except ValueError:
        return result.output


@pytest.fixture
def cli(api, monkeypatch):
    """Run commands against the in-process API client from the `api` fixture."""
    monkeypatch.setattr(cli_main, "build_client", lambda: api)
    return runner


def test_humanize_size():
    assert cli_main.humanize_size(0) == "0 B"
    assert cli_main.humanize_size(512) == "512 B"
    assert cli_main.humanize_size(2048) == "2.0 KB"
    assert cli_main.humanize_size(5 * 1024 * 1024) == "5.0 MB"


def test_humanize_age():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert cli_main.humanize_age(now - timedelta(seconds=5)).endswith("s ago")
    assert cli_main.humanize_age(now - timedelta(minutes=5)).endswith("m ago")
    assert cli_main.humanize_age(now - timedelta(hours=5)).endswith("h ago")
    assert cli_main.humanize_age(now - timedelta(days=5)).endswith("d ago")


def test_init_writes_a_config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(cli_main, "client_config_path", lambda: path)
    result = runner.invoke(
        cli_main.app,
        ["init", "--server", "http://spider.test:8181", "--token", TOKEN, "--device", "laptop"],
    )
    assert result.exit_code == 0
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data == {"server": "http://spider.test:8181", "token": TOKEN, "device": "laptop"}


def test_init_rejects_a_short_token(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(cli_main, "client_config_path", lambda: path)
    result = runner.invoke(
        cli_main.app, ["init", "--server", "http://x:8181", "--token", "short", "--device", "d"]
    )
    assert result.exit_code != 0
    assert "32" in combined(result)
    assert not path.exists()


def test_push_a_file(cli, tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7")
    result = cli.invoke(cli_main.app, ["push", str(path)])
    assert result.exit_code == 0
    assert "report.pdf" in result.output


def test_push_several_files(cli, tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(name)
    result = cli.invoke(cli_main.app, ["push", str(tmp_path / "a.txt"), str(tmp_path / "b.txt")])
    assert result.exit_code == 0
    assert "a.txt" in result.output
    assert "b.txt" in result.output


def test_push_text_with_the_text_option(cli):
    result = cli.invoke(cli_main.app, ["push", "-t", "ข้อความทดสอบ", "--name", "note"])
    assert result.exit_code == 0
    assert "note" in result.output


def test_push_from_stdin(cli):
    result = cli.invoke(cli_main.app, ["push", "-"], input="piped content\n")
    assert result.exit_code == 0


def test_push_a_missing_file_exits_nonzero(cli, tmp_path):
    result = cli.invoke(cli_main.app, ["push", str(tmp_path / "absent.bin")])
    assert result.exit_code != 0
    assert "absent.bin" in combined(result)


def test_ls_prints_tab_separated_values_when_not_a_terminal(cli, tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-")
    cli.invoke(cli_main.app, ["push", str(path)])
    result = cli.invoke(cli_main.app, ["ls"])
    assert result.exit_code == 0
    line = result.output.strip().splitlines()[0]
    assert "\t" in line
    assert "\x1b[" not in result.output
    assert line.split("\t")[2] == "report.pdf"


def test_ls_search(cli):
    cli.invoke(cli_main.app, ["push", "-t", "the invoice text", "--name", "memo"])
    cli.invoke(cli_main.app, ["push", "-t", "unrelated", "--name", "other"])
    result = cli.invoke(cli_main.app, ["ls", "-q", "invoice"])
    assert "memo" in result.output
    assert "other" not in result.output


def test_ls_filters_to_files_only(cli, tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"x")
    cli.invoke(cli_main.app, ["push", str(path)])
    cli.invoke(cli_main.app, ["push", "-t", "a note", "--name", "note"])
    result = cli.invoke(cli_main.app, ["ls", "--files"])
    assert "f.bin" in result.output
    assert "note" not in result.output


def test_an_unreachable_server_exits_with_code_8(monkeypatch, config):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        cli_main,
        "build_client",
        lambda: SpiderClient(config, transport=httpx.MockTransport(refuse)),
    )
    result = runner.invoke(cli_main.app, ["ls"])
    assert result.exit_code == 8


def test_pull_writes_the_file_into_the_working_directory(cli, tmp_path, monkeypatch):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.7 body")
    cli.invoke(cli_main.app, ["push", str(source)])

    destination = tmp_path / "downloads"
    destination.mkdir()
    monkeypatch.chdir(destination)
    result = cli.invoke(cli_main.app, ["pull", "latest"])
    assert result.exit_code == 0
    assert (destination / "report.pdf").read_bytes() == b"%PDF-1.7 body"


def test_pull_accepts_an_explicit_output_path(cli, tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"payload")
    cli.invoke(cli_main.app, ["push", str(source)])
    target = tmp_path / "elsewhere" / "renamed.bin"
    result = cli.invoke(cli_main.app, ["pull", "latest", "-o", str(target)])
    assert result.exit_code == 0
    assert target.read_bytes() == b"payload"


def test_pull_refuses_to_overwrite_without_force(cli, tmp_path, monkeypatch):
    source = tmp_path / "a.bin"
    source.write_bytes(b"new bytes")
    cli.invoke(cli_main.app, ["push", str(source)])

    destination = tmp_path / "downloads"
    destination.mkdir()
    (destination / "a.bin").write_bytes(b"existing")
    monkeypatch.chdir(destination)

    result = cli.invoke(cli_main.app, ["pull", "latest"])
    assert result.exit_code != 0
    assert (destination / "a.bin").read_bytes() == b"existing"

    forced = cli.invoke(cli_main.app, ["pull", "latest", "--force"])
    assert forced.exit_code == 0
    assert (destination / "a.bin").read_bytes() == b"new bytes"


def test_pull_of_an_unknown_reference_exits_with_code_4(cli):
    result = cli.invoke(cli_main.app, ["pull", "01JD3K7XABCDEFGHJKMNPQRSTV"])
    assert result.exit_code == 4


def test_pull_leaves_no_partial_file_when_the_reference_is_bad(cli, tmp_path, monkeypatch):
    # A directory of its own: the `store` fixture already put a "data" folder
    # in tmp_path, so tmp_path itself is never empty.
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    cli.invoke(cli_main.app, ["pull", "01JD3K7XABCDEFGHJKMNPQRSTV"])
    assert list(workdir.iterdir()) == []


def test_cat_writes_bytes_to_stdout(cli):
    cli.invoke(cli_main.app, ["push", "-t", "ข้อความจาก cat", "--name", "note"])
    result = cli.invoke(cli_main.app, ["cat", "latest"])
    assert result.exit_code == 0
    assert "ข้อความจาก cat" in result.output


def test_rm_deletes_an_item(cli, tmp_path):
    source = tmp_path / "gone.bin"
    source.write_bytes(b"x")
    cli.invoke(cli_main.app, ["push", str(source)])
    assert cli.invoke(cli_main.app, ["rm", "latest"]).exit_code == 0
    assert cli.invoke(cli_main.app, ["ls"]).output.strip() == ""


def test_rm_of_an_unknown_reference_exits_with_code_4(cli):
    assert cli.invoke(cli_main.app, ["rm", "01JD3K7XABCDEFGHJKMNPQRSTV"]).exit_code == 4


def test_verify_reports_a_healthy_store(cli, tmp_path):
    source = tmp_path / "ok.bin"
    source.write_bytes(b"x")
    cli.invoke(cli_main.app, ["push", str(source)])
    result = cli.invoke(cli_main.app, ["verify"])
    assert result.exit_code == 0
    assert "1" in result.output


def test_verify_exits_nonzero_when_a_blob_is_missing(cli, store, tmp_path):
    source = tmp_path / "doomed.bin"
    source.write_bytes(b"x")
    cli.invoke(cli_main.app, ["push", str(source)])
    item_id = cli.invoke(cli_main.app, ["ls"]).output.split("\t")[0]
    for path in (store.blobs_dir).rglob("*"):
        if path.is_file() and path.name.startswith(item_id):
            path.unlink()
    result = cli.invoke(cli_main.app, ["verify"])
    assert result.exit_code == 1
    assert "doomed.bin" in combined(result)


def test_copy_sends_what_is_on_the_clipboard(cli, monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "ข้อความยาวๆ จาก Mac")
    result = cli.invoke(cli_main.app, ["copy"])
    assert result.exit_code == 0
    listing = cli.invoke(cli_main.app, ["ls"]).output
    assert "text" in listing


def test_copy_then_paste_moves_text_between_machines(cli, monkeypatch):
    original = "ข้อความยาวๆ ที่ต้องส่งข้ามเครื่อง\nบรรทัดที่สอง\ttab"
    monkeypatch.setattr(pyperclip, "paste", lambda: original)
    assert cli.invoke(cli_main.app, ["copy"]).exit_code == 0

    landed = []
    monkeypatch.setattr(pyperclip, "copy", landed.append)
    result = cli.invoke(cli_main.app, ["paste"])
    assert result.exit_code == 0
    assert landed == [original]


def test_paste_accepts_a_reference(cli, monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "first")
    cli.invoke(cli_main.app, ["copy"])
    first_id = cli.invoke(cli_main.app, ["ls"]).output.split("\t")[0]
    monkeypatch.setattr(pyperclip, "paste", lambda: "second")
    cli.invoke(cli_main.app, ["copy"])

    landed = []
    monkeypatch.setattr(pyperclip, "copy", landed.append)
    assert cli.invoke(cli_main.app, ["paste", first_id]).exit_code == 0
    assert landed == ["first"]


def test_copy_refuses_an_empty_clipboard(cli, monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "")
    result = cli.invoke(cli_main.app, ["copy"])
    assert result.exit_code != 0
    assert "empty" in combined(result).lower()


def test_copy_explains_a_missing_clipboard_backend(cli, monkeypatch):
    def unavailable():
        raise pyperclip.PyperclipException("no backend")

    monkeypatch.setattr(pyperclip, "paste", unavailable)
    result = cli.invoke(cli_main.app, ["copy"])
    assert result.exit_code != 0
    assert "spider cat" in combined(result)


def test_paste_refuses_a_binary_item_and_points_at_pull(cli, tmp_path):
    binary = tmp_path / "photo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02")
    cli.invoke(cli_main.app, ["push", str(binary)])
    result = cli.invoke(cli_main.app, ["paste"])
    assert result.exit_code != 0
    assert "spider pull" in combined(result)


def test_paste_explains_a_missing_clipboard_backend(cli, monkeypatch):
    monkeypatch.setattr(pyperclip, "paste", lambda: "some text")
    cli.invoke(cli_main.app, ["copy"])

    def unavailable(_text):
        raise pyperclip.PyperclipException("no backend")

    monkeypatch.setattr(pyperclip, "copy", unavailable)
    result = cli.invoke(cli_main.app, ["paste"])
    assert result.exit_code != 0
    assert "spider cat" in combined(result)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../.bashrc", ".bashrc"),
        ("/etc/passwd", "passwd"),
        ("..\\..\\evil.exe", "evil.exe"),
        ("..", "download"),
        ("", "download"),
        ("dir/", "download"),
    ],
)
def test_safe_filename_never_escapes_the_folder(stored, expected):
    assert cli_main.safe_filename(stored) == expected


def test_pull_of_a_hostile_name_stays_in_the_working_directory(cli, tmp_path, monkeypatch):
    cli.invoke(cli_main.app, ["push", "-t", "payload", "--name", "../escaped.txt"])
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    result = cli.invoke(cli_main.app, ["pull", "latest"])
    assert result.exit_code == 0
    assert (workdir / "escaped.txt").read_bytes() == b"payload"
    assert not (tmp_path / "escaped.txt").exists()


def test_download_of_an_item_whose_blob_is_gone_raises_a_spider_error(api, store):
    pushed = api.push_bytes(b"x", kind=ItemKind.file, name="gone.bin")
    store.blob_path(pushed.id).unlink()
    with pytest.raises(SpiderError) as caught:
        api.download(pushed.id, io.BytesIO())
    assert "spider verify" in caught.value.message


def test_cat_of_a_missing_blob_prints_an_error_not_a_traceback(cli, store):
    cli.invoke(cli_main.app, ["push", "-t", "doomed", "--name", "doomed"])
    for blob in store.blobs_dir.rglob("*"):
        if blob.is_file() and blob.parent != store.tmp_dir:
            blob.unlink()
    result = cli.invoke(cli_main.app, ["cat", "latest"])
    assert result.exit_code != 0
    output = " ".join(combined(result).split())  # rich wraps long lines
    assert "Traceback" not in output
    assert "spider verify" in output
