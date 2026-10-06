import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from spider.core.config import MIN_TOKEN_LENGTH
from spider.core.errors import ErrorCode, SpiderError
from spider.server.app import create_app, create_app_from_env
from spider.server.auth import SESSION_COOKIE, bearer_from_header, token_matches
from spider.server.routes import _chunks, is_inline_safe
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


def test_inline_whitelist():
    assert is_inline_safe("image/png") is True
    assert is_inline_safe("application/pdf") is True
    assert is_inline_safe("text/plain; charset=utf-8") is True
    assert is_inline_safe("image/svg+xml") is False
    assert is_inline_safe("text/html") is False
    assert is_inline_safe("application/octet-stream") is False


def test_list_returns_newest_first(client):
    upload(client, b"1", name="one")
    upload(client, b"2", name="two")
    body = client.get("/api/items", headers=AUTH).json()
    assert [item["name"] for item in body["items"]] == ["two", "one"]
    assert body["next_before"] is None


def test_list_paginates(client):
    for n in range(5):
        upload(client, bytes([n]), name=f"n{n}")
    first = client.get("/api/items?limit=2", headers=AUTH).json()
    assert len(first["items"]) == 2
    assert first["next_before"] is not None
    second = client.get(
        f"/api/items?limit=2&before={first['next_before']}", headers=AUTH
    ).json()
    assert len(second["items"]) == 2
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 4


def test_list_filters_by_kind_and_search(client):
    upload(client, b"data", name="invoice.pdf")
    upload(client, b"other", name="cat.png")
    body = client.get("/api/items?q=invoice", headers=AUTH).json()
    assert [item["name"] for item in body["items"]] == ["invoice.pdf"]
    body = client.get("/api/items?kind=file&limit=50", headers=AUTH).json()
    assert len(body["items"]) == 2


def test_metadata_by_prefix(client):
    created = upload(client, b"x", name="x.bin").json()
    body = client.get(f"/api/items/{created['id'][:8]}", headers=AUTH).json()
    assert body["id"] == created["id"]


def test_metadata_latest(client):
    upload(client, b"old", name="old")
    newest = upload(client, b"new", name="new").json()
    assert client.get("/api/items/latest", headers=AUTH).json()["id"] == newest["id"]


def test_metadata_unknown_returns_404(client):
    response = client.get("/api/items/01JD3K7XABCDEFGHJKMNPQRSTV", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_ambiguous_prefix_returns_409(client):
    first = upload(client, b"a", name="a").json()
    upload(client, b"b", name="b")
    response = client.get(f"/api/items/{first['id'][:3]}", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ambiguous_id"


def test_download_returns_the_exact_bytes(client):
    payload = bytes(range(256))
    created = upload(client, payload, name="all-bytes.bin").json()
    response = client.get(f"/api/items/{created['id']}/content", headers=AUTH)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["x-spider-sha256"] == created["sha256"]
    assert response.headers["content-length"] == str(len(payload))


def test_download_defaults_to_attachment(client):
    created = upload(client, b"%PDF-", name="doc.pdf").json()
    response = client.get(f"/api/items/{created['id']}/content", headers=AUTH)
    assert response.headers["content-disposition"].startswith("attachment")


def test_inline_is_allowed_for_a_pdf(client):
    response = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": ("doc.pdf", b"%PDF-", "application/pdf")},
        data={"device": "test-device"},
    )
    item_id = response.json()["id"]
    headers = client.get(f"/api/items/{item_id}/content?disposition=inline", headers=AUTH).headers
    assert headers["content-disposition"].startswith("inline")


def test_inline_is_refused_for_html(client):
    response = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": ("page.html", b"<script>alert(1)</script>", "text/html")},
        data={"device": "test-device"},
    )
    item_id = response.json()["id"]
    headers = client.get(f"/api/items/{item_id}/content?disposition=inline", headers=AUTH).headers
    assert headers["content-disposition"].startswith("attachment")


def test_inline_is_refused_for_svg(client):
    response = client.post(
        "/api/items",
        headers=AUTH,
        files={"content": ("logo.svg", b"<svg onload=alert(1)>", "image/svg+xml")},
        data={"device": "test-device"},
    )
    item_id = response.json()["id"]
    headers = client.get(f"/api/items/{item_id}/content?disposition=inline", headers=AUTH).headers
    assert headers["content-disposition"].startswith("attachment")


def test_download_encodes_a_non_ascii_filename(client):
    created = upload(client, b"x", name="รายงาน.pdf").json()
    disposition = client.get(
        f"/api/items/{created['id']}/content", headers=AUTH
    ).headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition


def test_delete_removes_the_item(client):
    created = upload(client, b"x", name="x").json()
    assert client.delete(f"/api/items/{created['id']}", headers=AUTH).status_code == 204
    assert client.get(f"/api/items/{created['id']}", headers=AUTH).status_code == 404


def test_delete_unknown_returns_404(client):
    response = client.delete("/api/items/01JD3K7XABCDEFGHJKMNPQRSTV", headers=AUTH)
    assert response.status_code == 404


def test_verify_reports_missing_blobs(client, store):
    created = upload(client, b"x", name="x").json()
    assert client.get("/api/verify", headers=AUTH).json() == {"missing": []}
    store.blob_path(created["id"]).unlink()
    body = client.get("/api/verify", headers=AUTH).json()
    assert [item["id"] for item in body["missing"]] == [created["id"]]


def test_download_filename_strips_quotes_backslashes_and_control_characters(client):
    created = upload(client, b"x", name='a"b\\c\r\nd.txt').json()
    disposition = client.get(
        f"/api/items/{created['id']}/content", headers=AUTH
    ).headers["content-disposition"]
    assert disposition.startswith('attachment; filename="abcd.txt";')
    assert "\r" not in disposition and "\n" not in disposition
