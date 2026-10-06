import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from spider.core.config import MIN_TOKEN_LENGTH
from spider.core.errors import ErrorCode, SpiderError
from spider.server.app import create_app, create_app_from_env
from spider.server.auth import SESSION_COOKIE, bearer_from_header, token_matches
from spider.server.routes import _chunks
from spider.server.storage import Storage

TOKEN = "s" * MIN_TOKEN_LENGTH
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    return storage


@pytest.fixture
def client(store):
    with TestClient(create_app(store, TOKEN)) as test_client:
        yield test_client


def test_bearer_parsing():
    assert bearer_from_header("Bearer abc") == "abc"
    assert bearer_from_header("bearer abc") == "abc"
    assert bearer_from_header("Basic abc") is None
    assert bearer_from_header("Bearer ") is None
    assert bearer_from_header(None) is None


def test_token_matches_rejects_empty_and_wrong_values():
    assert token_matches(TOKEN, TOKEN) is True
    assert token_matches(TOKEN, None) is False
    assert token_matches(TOKEN, "") is False
    assert token_matches(TOKEN, TOKEN + "x") is False


def test_auth_uses_constant_time_comparison():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "src" / "spider" / "server" / "auth.py").read_text(encoding="utf-8")
    assert "compare_digest" in source


def test_healthz_needs_no_token(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_requires_a_token(client):
    response = client.get("/api/items")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_rejects_a_wrong_token(client):
    response = client.get("/api/items", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_api_accepts_the_right_token(client):
    assert client.get("/api/items", headers=AUTH).status_code == 200


def test_api_accepts_a_session_cookie(client):
    client.cookies.set(SESSION_COOKIE, TOKEN)
    assert client.get("/api/items").status_code == 200


def test_every_response_forbids_content_type_sniffing(client):
    assert client.get("/healthz").headers["x-content-type-options"] == "nosniff"


def test_error_payload_shape(client):
    body = client.get("/api/items").json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}


def test_create_app_from_env_refuses_a_short_token(tmp_path):
    with pytest.raises(RuntimeError, match="32"):
        create_app_from_env({"SPIDER_TOKEN": "short", "SPIDER_DATA_DIR": str(tmp_path)})


def test_create_app_from_env_refuses_a_missing_token(tmp_path):
    with pytest.raises(RuntimeError, match="SPIDER_TOKEN"):
        create_app_from_env({"SPIDER_DATA_DIR": str(tmp_path)})


def test_create_app_from_env_builds_a_working_app(tmp_path):
    app = create_app_from_env(
        {"SPIDER_TOKEN": TOKEN, "SPIDER_DATA_DIR": str(tmp_path / "d"), "SPIDER_MIN_FREE_GB": "0"}
    )
    with TestClient(app) as test_client:
        assert test_client.get("/healthz").status_code == 200


def upload(client, payload: bytes, *, name="thing.bin", kind="file", **fields):
    data = {"kind": kind, "name": name, "device": "test-device", **fields}
    return client.post(
        "/api/items",
        headers=AUTH,
        files={"content": (name, payload, "application/octet-stream")},
        data=data,
    )


def test_upload_returns_201_and_the_item(client):
    payload = b"hello over http"
    response = upload(client, payload, name="greeting.txt")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "greeting.txt"
    assert body["size"] == len(payload)
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["source_device"] == "test-device"
    assert len(body["id"]) == 26


def test_upload_requires_a_token(client):
    response = client.post(
        "/api/items",
        files={"content": ("x.bin", b"x", "application/octet-stream")},
        data={"name": "x.bin"},
    )
    assert response.status_code == 401


def test_upload_accepts_a_matching_checksum(client):
    payload = b"verified"
    response = upload(client, payload, sha256=hashlib.sha256(payload).hexdigest())
    assert response.status_code == 201


def test_upload_rejects_a_wrong_checksum(client):
    response = upload(client, b"real", sha256="f" * 64)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "checksum_mismatch"


def test_upload_of_text_sets_a_text_content_type_and_preview(client):
    response = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": ("note", "สวัสดี".encode(), "application/octet-stream")},
        data={"kind": "text", "name": "note", "device": "test-device"},
    )
    body = response.json()
    assert body["kind"] == "text"
    assert body["content_type"] == "text/plain; charset=utf-8"
    assert body["preview"] == "สวัสดี"


def test_upload_falls_back_to_the_uploaded_filename(client):
    response = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": ("from-disk.pdf", b"%PDF-", "application/pdf")},
        data={"device": "test-device"},
    )
    assert response.json()["name"] == "from-disk.pdf"
    assert response.json()["content_type"] == "application/pdf"


def test_upload_is_refused_when_the_disk_is_nearly_full(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=1_000_000)
    storage.init()
    with TestClient(create_app(storage, TOKEN)) as full_client:
        response = upload(full_client, b"anything")
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "disk_full"


def test_upload_is_refused_above_the_configured_size_limit(tmp_path):
    storage = Storage(tmp_path / "data", min_free_gb=0)
    storage.init()
    app = create_app(storage, TOKEN, max_item_mb=1)
    with TestClient(app) as limited_client:
        response = upload(limited_client, b"z" * (2 * 1024 * 1024))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_size_limit_is_enforced_on_the_bytes_actually_received():
    """Content-Length can be absent (chunked uploads), so count while streaming."""
    stream = _chunks(io.BytesIO(b"z" * 10), size=4, limit=8)
    with pytest.raises(SpiderError) as caught:
        list(stream)
    assert caught.value.code is ErrorCode.bad_request


def test_an_invalid_form_field_uses_the_standard_error_shape(client):
    response = upload(client, b"x", kind="bogus")
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "bad_request"
    assert "kind" in body["error"]["message"]


def test_a_missing_upload_field_uses_the_standard_error_shape(client):
    response = client.post("/api/items", headers=AUTH, data={"name": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_auth_is_checked_before_form_validation(client):
    response = client.post("/api/items", data={"kind": "bogus"})
    assert response.status_code == 401
