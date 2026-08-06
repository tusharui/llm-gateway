import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware.auth import AuthMiddleware, hash_string, requires_auth
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.logging import LoggingMiddleware


@pytest.fixture(autouse=True)
def dev_key(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_key", "sk-gateway-test-key")


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(LoggingMiddleware)

    @app.post("/chat")
    async def chat(request: Request):
        return {"ok": True, "api_key_id": getattr(request.state, "api_key_id", None)}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/boom")
    async def boom():
        raise ValueError("kaboom")

    return app


def test_health_is_public():
    client = TestClient(build_app())
    assert client.get("/health").status_code == 200


def test_missing_auth_is_401_json():
    client = TestClient(build_app())
    r = client.post("/chat")
    assert r.status_code == 401
    assert "detail" in r.json()


def test_valid_gateway_key_passes():
    client = TestClient(build_app())
    r = client.post("/chat", headers={"Authorization": "Bearer sk-gateway-test-key"})
    assert r.status_code == 200
    assert r.json()["api_key_id"] == "gateway_admin"


def test_wrong_gateway_key_is_401():
    client = TestClient(build_app())
    r = client.post("/chat", headers={"Authorization": "Bearer sk-gateway-wrong"})
    assert r.status_code == 401


def test_malformed_auth_header_is_401():
    client = TestClient(build_app())
    assert client.post("/chat", headers={"Authorization": "Token abc"}).status_code == 401


def test_unhandled_error_is_500_with_request_id():
    client = TestClient(build_app())
    r = client.get("/boom", headers={"X-Request-ID": "test-req-1"})
    assert r.status_code == 500
    assert r.json()["error"]["request_id"] == "test-req-1"
    assert r.headers.get("x-request-id") == "test-req-1"


def test_401_echoes_request_id():
    client = TestClient(build_app())
    r = client.post("/chat", headers={"X-Request-ID": "abc"})
    assert r.status_code == 401
    assert r.headers.get("x-request-id") == "abc"


def test_requires_auth_paths():
    assert requires_auth("/chat")
    assert requires_auth("/chat/abc")
    assert requires_auth("/embeddings")
    assert requires_auth("/batch")
    assert not requires_auth("/health")
    assert not requires_auth("/chat-history/sessions")
    assert not requires_auth("/models")


def test_hash_string_is_deterministic():
    assert hash_string("secret") == hash_string("secret")
    assert hash_string("a") != hash_string("b")
