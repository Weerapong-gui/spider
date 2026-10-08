import hashlib

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from spider.core.config import MIN_TOKEN_LENGTH
from spider.server.app import create_app
from spider.server.storage import Storage

TOKEN = "r" * MIN_TOKEN_LENGTH
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    storage = Storage(tmp_path_factory.mktemp("roundtrip"), min_free_gb=0)
    storage.init()
    with TestClient(create_app(storage, TOKEN)) as test_client:
        yield test_client


def roundtrip(client, payload: bytes, *, name="blob.bin", kind="file"):
    created = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": (name, payload, "application/octet-stream")},
        data={"kind": kind, "name": name, "device": "test-device"},
    ).json()
    fetched = client.get(f"/api/items/{created['id']}/content", headers=AUTH)
    return created, fetched


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payload=st.binary(min_size=0, max_size=200_000))
def test_arbitrary_bytes_survive_a_roundtrip(client, payload):
    created, fetched = roundtrip(client, payload)
    assert fetched.content == payload
    assert created["size"] == len(payload)
    assert created["sha256"] == hashlib.sha256(payload).hexdigest()
    assert fetched.headers["x-spider-sha256"] == created["sha256"]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"\xff",
        b"before\x00after",
        b"\r\n\r\n",
        bytes(range(256)),
        b"\xff\xfe" + "utf-16 looking bytes".encode("utf-16-le"),
    ],
    ids=["empty", "null", "high", "embedded-null", "crlf", "all-bytes", "utf16-bom"],
)
def test_awkward_byte_patterns(client, payload):
    _, fetched = roundtrip(client, payload)
    assert fetched.content == payload


@pytest.mark.parametrize(
    "text",
    [
        "สวัสดีครับ ทดสอบภาษาไทย",
        "emoji 🕸️🐍 mixed with ภาษาไทย and 中文",
        "line one\r\nline two\r\nline three",
        "tab\tseparated\tvalues",
        "trailing whitespace   ",
    ],
    ids=["thai", "mixed-emoji", "crlf-text", "tabs", "trailing-space"],
)
def test_text_survives_a_roundtrip_unchanged(client, text):
    payload = text.encode("utf-8")
    created, fetched = roundtrip(client, payload, name="note", kind="text")
    assert fetched.content == payload
    assert fetched.content.decode("utf-8") == text
    assert created["preview"] == text[:200]


def test_crlf_is_not_converted_to_lf(client):
    payload = b"a\r\nb"
    _, fetched = roundtrip(client, payload, name="crlf.txt", kind="text")
    assert b"\r\n" in fetched.content
    assert fetched.content == payload


@pytest.mark.parametrize(
    "name",
    [
        "รายงานประจำปี.pdf",
        "file with spaces.txt",
        "emoji 🕸️ name.bin",
        'quote"inside.txt',
        "ยาวมาก" * 20 + ".txt",
    ],
    ids=["thai", "spaces", "emoji", "quote", "long-thai"],
)
def test_awkward_filenames_roundtrip(client, name):
    created, fetched = roundtrip(client, b"payload", name=name)
    assert created["name"] == name
    assert fetched.content == b"payload"
    assert client.get(f"/api/items/{created['id']}", headers=AUTH).json()["name"] == name


@pytest.mark.slow
def test_a_fifty_megabyte_file_survives_a_roundtrip(client):
    payload = bytes(range(256)) * (50 * 1024 * 1024 // 256)
    created, fetched = roundtrip(client, payload, name="big.bin")
    assert created["size"] == len(payload)
    assert hashlib.sha256(fetched.content).hexdigest() == created["sha256"]
