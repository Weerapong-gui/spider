import pytest
from fastapi.testclient import TestClient

from spider.core.config import MIN_TOKEN_LENGTH
from spider.server.app import create_app, create_app_from_env
from spider.server.auth import SESSION_COOKIE, bearer_from_header, token_matches
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
