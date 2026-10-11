"""Authorization boundaries.

The audit found that ``requires_auth`` was a three-prefix string match, so
every route outside ``/chat``, ``/embeddings`` and ``/batch`` was reachable
with no credential at all. ``POST /api-keys`` -- which mints a working
credential -- returned 200 to an anonymous caller and handed back a live key.

These tests pin the boundary from the outside: real requests through the real
middleware stack, asserting status codes. They do not assert on
``requires_auth`` alone, because a prefix list that reads correctly in
isolation is exactly how the original bug was invisible.

Two rules for anything added here:

1. Assert the status code, not the internals. A test that asserts
   ``requires_auth("/api-keys")`` passes even if the middleware forgets to
   call it.
2. When a session is needed, stub ``get_session`` on the module that imported
   it. ``from app.database import get_session`` binds the name at import time,
   so patching the source module does nothing.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware.auth import (
    ADMIN_KEY_ID,
    AuthMiddleware,
    RateLimitMiddleware,
    requires_admin,
    requires_auth,
)
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.logging import LoggingMiddleware

ADMIN = "sk-gateway-admin-key"
TENANT = "sk-gw-tenant-key"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_key", ADMIN)


class FakeKey:
    def __init__(self, key_id="tenant-1", is_active=True):
        self.id = key_id
        self.is_active = is_active
        self.last_used_at = None


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, value=None):
        self._value = value

    async def execute(self, *a, **k):
        return FakeResult(self._value)

    async def commit(self):
        pass

    async def close(self):
        pass


def patch_store(monkeypatch, session):
    import app.middleware.auth as auth

    async def get_session():
        return session

    monkeypatch.setattr(auth, "get_session", get_session)


def build_app() -> FastAPI:
    """A stand-in for the real app with the same middleware order."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(LoggingMiddleware)

    # Every route below stands in for a real one. The handler body is
    # irrelevant: the point is whether the request is allowed to reach it.
    @app.api_route("/chat", methods=["GET", "POST"])
    @app.api_route("/chat-history/sessions", methods=["GET", "POST"])
    @app.api_route("/chat-history/sessions/{sid}", methods=["GET", "PATCH", "DELETE"])
    @app.api_route("/api-keys", methods=["GET", "POST"])
    @app.api_route("/api-keys/{kid}", methods=["GET", "PATCH", "DELETE"])
    @app.api_route("/api-keys/{kid}/toggle", methods=["POST"])
    @app.api_route("/analytics/summary", methods=["GET"])
    @app.api_route("/analytics/recent", methods=["GET"])
    @app.api_route("/batch/chat", methods=["POST"])
    @app.api_route("/embeddings", methods=["POST"])
    @app.api_route("/health", methods=["GET"])
    @app.api_route("/ready", methods=["GET"])
    @app.api_route("/models", methods=["GET"])
    async def anything(request: Request):
        return {"ok": True, "api_key_id": getattr(request.state, "api_key_id", None)}

    return app


# --- the original vulnerability, stated as a test ---------------------------


def test_api_key_creation_requires_a_credential(monkeypatch):
    """POST /api-keys returned 200 and a live key to an anonymous caller."""
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    r = client.post("/api-keys", json={"name": "attacker"})

    assert r.status_code == 401, (
        "key creation is reachable without a credential, so anyone can mint "
        "a working key and spend the owner's provider budget"
    )


def test_api_key_listing_requires_a_credential(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.get("/api-keys").status_code == 401


def test_api_key_deletion_requires_a_credential(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.delete("/api-keys/some-id").status_code == 401


def test_analytics_requires_a_credential(monkeypatch):
    """Aggregate spend and per-request rows are not public."""
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.get("/analytics/summary").status_code == 401
    assert client.get("/analytics/recent").status_code == 401


def test_chat_history_requires_a_credential(monkeypatch):
    """/chat-history was excluded from auth by an explicit guard.

    It was excluded because ``"/chat-history".startswith("/chat")`` is True
    and the original expression tried to carve out that collision. The carve-out
    threw away authentication for the whole prefix. Conversation transcripts
    are user data.
    """
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.get("/chat-history/sessions").status_code == 401
    assert client.post("/chat-history/sessions", json={}).status_code == 401
    assert client.delete("/chat-history/sessions/abc").status_code == 401


# --- the second vulnerability ------------------------------------------------


def test_store_unavailable_fails_closed(monkeypatch):
    """A token the store could not verify must not be honoured.

    Previously any non-admin token was accepted whenever the key store was
    unreachable, so a typo in DATABASE_URL handed arbitrary callers the
    owner's provider spend.
    """
    patch_store(monkeypatch, None)  # store down
    client = TestClient(build_app())

    r = client.post("/chat", headers={"Authorization": "Bearer total-garbage"})

    assert r.status_code == 503, (
        "an unverifiable token was accepted; that is an authentication "
        "bypass gated only on the database being unreachable"
    )
    assert "Retry-After" in r.headers


def test_store_unavailable_still_admits_the_admin_key(monkeypatch):
    """Break-glass access must survive the exact failure that causes the 503."""
    patch_store(monkeypatch, None)
    client = TestClient(build_app())

    r = client.post("/chat", headers={"Authorization": f"Bearer {ADMIN}"})

    assert r.status_code == 200
    assert r.json()["api_key_id"] == ADMIN_KEY_ID


def test_store_unavailable_does_not_admit_a_lookalike_admin_key(monkeypatch):
    """The admin path is constant-time compare; it is still a real compare."""
    patch_store(monkeypatch, None)
    client = TestClient(build_app())

    r = client.post("/chat", headers={"Authorization": "Bearer sk-gateway-wrong"})

    assert r.status_code == 401


def test_store_error_fails_closed_too(monkeypatch):
    """A session that raises is the same fact as a session that is absent."""

    class Exploding:
        async def execute(self, *a, **k):
            raise RuntimeError("connection reset")

        async def commit(self):
            pass

        async def close(self):
            pass

    patch_store(monkeypatch, Exploding())
    client = TestClient(build_app())

    assert client.post("/chat", headers={"Authorization": "Bearer whatever"}).status_code == 503


# --- admin vs tenant ---------------------------------------------------------


def test_tenant_key_cannot_reach_admin_routes(monkeypatch):
    """A valid key is not sufficient for key management or billing data."""
    patch_store(monkeypatch, FakeSession(FakeKey("tenant-1")))
    client = TestClient(build_app())
    tenant = {"Authorization": f"Bearer {TENANT}"}

    r = client.post("/api-keys", json={"name": "x"}, headers=tenant)
    assert r.status_code == 403, "a tenant key minted a new credential"

    assert client.get("/api-keys", headers=tenant).status_code == 403
    assert client.delete("/api-keys/abc", headers=tenant).status_code == 403
    assert client.get("/analytics/summary", headers=tenant).status_code == 403


def test_tenant_key_reaches_tenant_routes(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey("tenant-1")))
    client = TestClient(build_app())
    tenant = {"Authorization": f"Bearer {TENANT}"}

    for method, path in [
        ("post", "/chat"),
        ("post", "/embeddings"),
        ("post", "/batch/chat"),
        ("get", "/chat-history/sessions"),
    ]:
        r = getattr(client, method)(path, headers=tenant)
        assert r.status_code == 200, f"{method} {path} rejected a valid tenant key"


def test_admin_key_reaches_admin_routes(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())
    admin = {"Authorization": f"Bearer {ADMIN}"}

    assert client.post("/api-keys", json={}, headers=admin).status_code == 200
    assert client.get("/analytics/summary", headers=admin).status_code == 200


def test_disabled_tenant_key_is_rejected(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey("tenant-1", is_active=False)))
    client = TestClient(build_app())

    r = client.post("/chat", headers={"Authorization": f"Bearer {TENANT}"})

    assert r.status_code == 401


# --- what stays public --------------------------------------------------------


def test_operational_endpoints_stay_public(monkeypatch):
    """Orchestrators poll these without credentials; auth would break deploys."""
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_model_catalogue_stays_public(monkeypatch):
    """No user data and no provider call; it is a capability advertisement."""
    patch_store(monkeypatch, FakeSession(FakeKey()))
    client = TestClient(build_app())

    assert client.get("/models").status_code == 200


# --- prefix-matching invariants ---------------------------------------------


def test_prefix_collisions_do_not_leak_auth():
    """A route that merely starts with an authenticated prefix.

    This is the bug that produced the /chat-history carve-out. If a future
    route named /chat-anything or /analytics-internal is added, these
    assertions are what stop it from being public or, worse, silently
    inheriting admin rights.
    """
    assert requires_auth("/chat") is True
    assert requires_auth("/chat-history") is True
    assert requires_auth("/chat-history/sessions") is True
    assert requires_auth("/chat-history/sessions/abc/messages") is True
    assert requires_auth("/api-keys") is True
    assert requires_auth("/api-keys/abc") is True
    assert requires_auth("/analytics/summary") is True

    assert requires_auth("/health") is False
    assert requires_auth("/ready") is False
    assert requires_auth("/models") is False
    assert requires_auth("/routing") is False
    assert requires_auth("/ws/chat") is False
    assert requires_auth("/") is False


def test_admin_set_is_a_subset_of_the_authenticated_set():
    """An admin route that is not authenticated would be unreachable-by-design."""
    for path in ("/api-keys", "/api-keys/abc", "/analytics/summary"):
        assert requires_admin(path) is True
        assert requires_auth(path) is True