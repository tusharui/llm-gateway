"""Dashboard user auth: passwords, sessions, and the /auth routes.

Two layers. The primitives (hashing, tokens, verify_login) are tested with no
database. The routes are driven over real HTTP through the real router with a
stubbed session, asserting status codes and cookie attributes.

Conventions borrowed from test_authz.py: assert the observable result rather
than internals, and patch get_session on the module that imported it, since
`from app.database import get_session` binds the name at import time.

verify_login tests use asyncio.run rather than an async plugin:
pytest-asyncio is not in requirements-dev.txt, and adding one (plus the
asyncio_mode config it needs) for five coroutines is a worse trade than one
call per test. Same approach as tests/test_migrations.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routes.auth import public_user, router as auth_router
from app.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    normalize_email,
    verify_login,
    verify_password,
)


class TestPasswordHashing:
    def test_roundtrip(self):
        encoded = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", encoded)

    def test_wrong_password_fails(self):
        encoded = hash_password("correct horse battery staple")
        assert not verify_password("Correct horse battery staple", encoded)
        assert not verify_password("", encoded)
        assert not verify_password("correct horse battery stapl", encoded)

    def test_salt_makes_identical_passwords_differ(self):
        a = hash_password("hunter2-hunter2")
        b = hash_password("hunter2-hunter2")
        assert a != b
        assert verify_password("hunter2-hunter2", a)
        assert verify_password("hunter2-hunter2", b)

    def test_plaintext_is_absent_from_the_hash(self):
        assert "hunter2-hunter2" not in hash_password("hunter2-hunter2")

    def test_scheme_and_cost_are_recorded(self):
        scheme, iterations, salt, digest = hash_password("whatever-1234").split("$")
        assert scheme == "pbkdf2_sha256"
        assert int(iterations) == settings.password_hash_iterations
        assert salt and digest

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-a-hash",
            "pbkdf2_sha256$notanumber$c2FsdA==$aGFzaA==",
            "pbkdf2_sha256$600000$!!!$!!!",
            "pbkdf2_sha256$600000",
            "argon2$1$abc$def",
        ],
    )
    def test_malformed_hash_is_a_failed_verification_not_a_crash(self, bad):
        assert not verify_password("whatever", bad)

    def test_a_hash_from_another_scheme_does_not_verify(self):
        scrambled = DUMMY_PASSWORD_HASH.replace("pbkdf2_sha256", "scrypt")
        assert not verify_password("pw12345678", scrambled)

    def test_needs_rehash_detects_raised_cost(self, monkeypatch):
        encoded = hash_password("password123")
        assert not needs_rehash(encoded)

        monkeypatch.setattr(settings, "password_hash_iterations", 10_000_000)
        assert needs_rehash(encoded)
        assert verify_password("password123", encoded)

    def test_needs_rehash_is_false_for_garbage(self):
        assert not needs_rehash("nonsense")

    def test_dummy_hash_rejects_anything(self):
        assert not verify_password("", DUMMY_PASSWORD_HASH)


class TestSessionTokens:
    def test_token_is_not_stored_in_the_clear(self):
        raw, digest = new_session_token()
        assert raw != digest
        assert hash_token(raw) == digest
        assert raw not in digest

    def test_tokens_are_unique(self):
        tokens = {new_session_token()[0] for _ in range(100)}
        assert len(tokens) == 100

    def test_tokens_are_url_safe(self):
        for _ in range(20):
            raw, _ = new_session_token()
            assert all(c.isalnum() or c in "-_" for c in raw)

    def test_email_normalization(self):
        assert normalize_email("  Ada@Example.COM ") == "ada@example.com"
        assert normalize_email("ADA@example.com") == normalize_email("ada@EXAMPLE.com")


class FakeResult:
    def __init__(self, value=None, row=None):
        self._value = value
        self._row = row

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._row


class FakeSession:
    def __init__(self, user=None, session_row=None):
        self.user = user
        self.session_row = session_row
        self.added = []
        self.committed = False
        self.executed = []
        self.deleted = False

    async def execute(self, statement, *a, **k):
        self.executed.append(statement)
        sql = str(statement)
        if "auth_sessions" in sql and "JOIN" in sql.upper():
            return FakeResult(row=self.session_row)
        if sql.strip().upper().startswith("DELETE"):
            self.deleted = True
            return FakeResult()
        return FakeResult(value=self.user)

    async def flush(self):
        pass

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.committed = False

    async def close(self):
        pass


def make_user(**kw):
    defaults = dict(
        id="u1",
        email="ada@example.com",
        password_hash=hash_password("password123"),
        is_active=True,
        is_admin=False,
        created_at=datetime.now(timezone.utc),
        last_login_at=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def make_session(**kw):
    defaults = dict(
        id="s1",
        user_id="u1",
        token_hash=hash_token("raw-token"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        last_used_at=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestVerifyLogin:
    def test_correct_password(self):
        user = make_user()
        session = FakeSession(user=user)
        assert asyncio.run(verify_login(session, "ADA@example.com", "password123")) is user

    def test_wrong_password(self):
        session = FakeSession(user=make_user())
        assert asyncio.run(verify_login(session, "ada@example.com", "wrong")) is None

    def test_unknown_user(self):
        session = FakeSession(user=None)
        found = asyncio.run(verify_login(session, "nobody@example.com", "password123"))
        assert found is None

    def test_unknown_user_still_hashes(self, monkeypatch):
        import app.security as security

        calls = []
        real = security.verify_password

        def counting(password, encoded):
            calls.append(encoded)
            return real(password, encoded)

        monkeypatch.setattr(security, "verify_password", counting)

        session = FakeSession(user=None)
        assert asyncio.run(
            verify_login(session, "nobody@example.com", "password123")
        ) is None
        assert calls, "an unknown email skipped password hashing entirely"
        assert calls[0] == security.DUMMY_PASSWORD_HASH

    def test_lookup_binds_the_normalized_address(self):
        session = FakeSession(user=None)
        asyncio.run(verify_login(session, "  Ada@Example.COM  ", "password123"))
        bound = session.executed[0].compile().params
        assert "ada@example.com" in bound.values()


def build_app(monkeypatch, session) -> FastAPI:
    import app.routes.auth as auth_routes
    import app.security as security

    async def get_session():
        return session

    monkeypatch.setattr(auth_routes, "get_session", get_session)
    monkeypatch.setattr(security, "get_session", get_session)

    app = FastAPI()
    app.include_router(auth_router)
    return app


@pytest.fixture
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_max", 10_000)


@pytest.fixture(autouse=True)
def dev_cookies(monkeypatch):
    monkeypatch.setattr(settings, "node_env", "development")


class TestRegister:
    def test_creates_a_user_and_sets_a_cookie(self, monkeypatch, no_rate_limit):
        session = FakeSession(user=None)
        client = TestClient(build_app(monkeypatch, session))

        res = client.post(
            "/auth/register", json={"email": "Ada@Example.com", "password": "password123"}
        )

        assert res.status_code == 201
        assert res.json()["user"]["email"] == "ada@example.com"
        assert session.added and session.added[0].password_hash != "password123"

        cookie = res.headers["set-cookie"]
        assert settings.session_cookie_name in cookie
        assert "HttpOnly" in cookie
        assert f"Max-Age={settings.session_ttl_seconds}" in cookie

    def test_response_never_contains_a_hash(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        res = client.post(
            "/auth/register", json={"email": "a@b.com", "password": "password123"}
        )
        assert "password_hash" not in res.text

    def test_is_never_admin(self, monkeypatch, no_rate_limit):
        session = FakeSession(user=None)
        client = TestClient(build_app(monkeypatch, session))
        res = client.post(
            "/auth/register",
            json={"email": "a@b.com", "password": "password123", "is_admin": True},
        )
        assert res.status_code == 201
        assert res.json()["user"]["is_admin"] is False
        assert session.added[0].is_admin is False

    def test_duplicate_is_409(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=make_user())))
        res = client.post(
            "/auth/register", json={"email": "ada@example.com", "password": "password123"}
        )
        assert res.status_code == 409

    def test_disabled_registration_is_403(self, monkeypatch, no_rate_limit):
        monkeypatch.setattr(settings, "allow_registration", False)
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        res = client.post(
            "/auth/register", json={"email": "a@b.com", "password": "password123"}
        )
        assert res.status_code == 403

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "nope", "password": "password123"},
            {"email": "a@b.com", "password": "short"},
            {"email": "", "password": "password123"},
            {"email": "a@b.com", "password": ""},
        ],
    )
    def test_invalid_input_is_422(self, monkeypatch, no_rate_limit, payload):
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        assert client.post("/auth/register", json=payload).status_code == 422


class TestLogin:
    def test_valid_credentials_set_a_cookie(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=make_user())))
        res = client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "password123"}
        )
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "ada@example.com"
        assert settings.session_cookie_name in res.headers["set-cookie"]

    def test_wrong_password_is_401(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=make_user())))
        res = client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "nope12345"}
        )
        assert res.status_code == 401
        assert "set-cookie" not in res.headers

    def test_unknown_user_gets_the_same_answer(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        res = client.post(
            "/auth/login", json={"email": "nobody@example.com", "password": "password123"}
        )
        assert res.status_code == 401
        assert res.json()["detail"] == "Invalid email or password"

    def test_deactivated_user_is_401_not_403(self, monkeypatch, no_rate_limit):
        user = make_user(is_active=False)
        client = TestClient(build_app(monkeypatch, FakeSession(user=user)))
        res = client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "password123"}
        )
        assert res.status_code == 401
        assert res.json()["detail"] == "Invalid email or password"

    def test_short_password_is_still_attempted(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=make_user())))
        res = client.post("/auth/login", json={"email": "ada@example.com", "password": "x"})
        assert res.status_code == 401

    def test_response_is_not_cacheable(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=make_user())))
        res = client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "password123"}
        )
        assert "no-store" in res.headers.get("cache-control", "")

    def test_upgrades_a_stale_hash_on_login(self, monkeypatch, no_rate_limit):
        user = make_user()
        weak = hash_password("password123")
        user.password_hash = weak
        monkeypatch.setattr(settings, "password_hash_iterations", 600_001)

        client = TestClient(build_app(monkeypatch, FakeSession(user=user)))
        res = client.post(
            "/auth/login", json={"email": "ada@example.com", "password": "password123"}
        )
        assert res.status_code == 200
        assert user.password_hash != weak
        assert verify_password("password123", user.password_hash)


class TestMe:
    def test_no_cookie_is_401(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        assert client.get("/auth/me").status_code == 401

    def test_valid_session_returns_the_user(self, monkeypatch, no_rate_limit):
        user = make_user()
        session = FakeSession(user=user, session_row=(user, make_session()))
        client = TestClient(build_app(monkeypatch, session))
        client.cookies.set(settings.session_cookie_name, "raw-token")

        res = client.get("/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["email"] == "ada@example.com"

    def test_expired_session_is_401(self, monkeypatch, no_rate_limit):
        user = make_user()
        expired = make_session(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        session = FakeSession(user=user, session_row=(user, expired))
        client = TestClient(build_app(monkeypatch, session))
        client.cookies.set(settings.session_cookie_name, "raw-token")

        assert client.get("/auth/me").status_code == 401

    def test_unknown_token_is_401(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        client.cookies.set(settings.session_cookie_name, "made-up")
        assert client.get("/auth/me").status_code == 401

    def test_deactivated_user_invalidates_a_live_session(self, monkeypatch, no_rate_limit):
        user = make_user(is_active=False)
        session = FakeSession(user=user, session_row=(user, make_session()))
        client = TestClient(build_app(monkeypatch, session))
        client.cookies.set(settings.session_cookie_name, "raw-token")
        assert client.get("/auth/me").status_code == 401

    def test_database_unavailable_is_503(self, monkeypatch, no_rate_limit):
        import app.security as security

        # Patch after build_app: the helper sets both modules' get_session, so
        # patching first would be silently undone by the build.
        client = TestClient(build_app(monkeypatch, FakeSession()))

        async def no_session():
            return None

        monkeypatch.setattr(security, "get_session", no_session)

        client.cookies.set(settings.session_cookie_name, "raw-token")
        assert client.get("/auth/me").status_code == 503


class TestLogout:
    def test_deletes_the_session_row_and_clears_the_cookie(self, monkeypatch, no_rate_limit):
        user = make_user()
        session = FakeSession(user=user, session_row=(user, make_session()))
        client = TestClient(build_app(monkeypatch, session))
        client.cookies.set(settings.session_cookie_name, "raw-token")

        res = client.post("/auth/logout")
        assert res.status_code == 200
        assert session.deleted
        cookie = res.headers["set-cookie"]
        assert settings.session_cookie_name in cookie
        assert "Max-Age=0" in cookie

    def test_requires_authentication(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession()))
        assert client.post("/auth/logout").status_code == 401


class TestChangePassword:
    def _client(self, monkeypatch, session):
        client = TestClient(build_app(monkeypatch, session))
        client.cookies.set(settings.session_cookie_name, "raw-token")
        return client

    def test_requires_the_current_password(self, monkeypatch, no_rate_limit):
        user = make_user()
        session = FakeSession(user=user, session_row=(user, make_session()))
        res = self._client(monkeypatch, session).post(
            "/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "newpassword1"},
        )
        assert res.status_code == 401

    def test_happy_path_rehashes(self, monkeypatch, no_rate_limit):
        user = make_user()
        original = user.password_hash
        session = FakeSession(user=user, session_row=(user, make_session()))
        res = self._client(monkeypatch, session).post(
            "/auth/change-password",
            json={"current_password": "password123", "new_password": "newpassword1"},
        )
        assert res.status_code == 200
        assert user.password_hash != original
        assert verify_password("newpassword1", user.password_hash)

    def test_short_new_password_is_422(self, monkeypatch, no_rate_limit):
        user = make_user()
        session = FakeSession(user=user, session_row=(user, make_session()))
        res = self._client(monkeypatch, session).post(
            "/auth/change-password",
            json={"current_password": "password123", "new_password": "short"},
        )
        assert res.status_code == 422

    def test_requires_authentication(self, monkeypatch, no_rate_limit):
        client = TestClient(build_app(monkeypatch, FakeSession()))
        res = client.post(
            "/auth/change-password",
            json={"current_password": "password123", "new_password": "newpassword1"},
        )
        assert res.status_code == 401


class TestPublicUserShape:
    def test_excludes_secrets(self):
        payload = public_user(make_user())
        assert "password_hash" not in payload
        assert set(payload) == {
            "id",
            "email",
            "is_active",
            "is_admin",
            "created_at",
            "last_login_at",
        }


class TestAuthRateLimit:
    def test_repeated_attempts_are_throttled(self, monkeypatch):
        import app.middleware.auth as auth_middleware

        monkeypatch.setattr(auth_middleware, "rate_limit_store", {})
        monkeypatch.setattr(settings, "auth_rate_limit_max", 3)

        client = TestClient(build_app(monkeypatch, FakeSession(user=None)))
        codes = [
            client.post(
                "/auth/login", json={"email": "ada@example.com", "password": "guess"}
            ).status_code
            for _ in range(5)
        ]
        assert 429 in codes, codes
        assert codes.index(429) >= 2

    def test_budgets_are_separate_per_ip(self, monkeypatch):
        import app.security as security

        monkeypatch.setattr(settings, "trust_forwarded_for", False)

        def request_from(ip):
            return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})

        first = security.client_host(request_from("10.0.0.1"))
        second = security.client_host(request_from("10.0.0.2"))
        assert first != second


class TestForwardedFor:
    def _request(self, headers):
        return SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"), headers=headers)

    def test_ignored_unless_trusted(self, monkeypatch):
        import app.security as security

        monkeypatch.setattr(settings, "trust_forwarded_for", False)
        request = self._request({"x-forwarded-for": "1.2.3.4"})
        assert security.client_host(request) == "10.0.0.9"

    def test_honoured_when_the_operator_opts_in(self, monkeypatch):
        import app.security as security

        monkeypatch.setattr(settings, "trust_forwarded_for", True)
        request = self._request({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
        assert security.client_host(request) == "1.2.3.4"