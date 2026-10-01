"""Token validation tests for the shared check_api_key path.

The websocket handshake needs the same answer to "is this token valid" that
HTTP gets, without the same response type: a 401 for a request, a close frame
for a socket. Extracting the validation into one function is what keeps those
from drifting, and these tests pin the three outcomes it can return.

Coroutines are driven with asyncio.run rather than pytest-asyncio, which this
project does not depend on.
"""

import asyncio

import pytest

from app.middleware.auth import (
    TokenCheck,
    TokenStatus,
    check_api_key,
    hash_string,
    parse_bearer,
    requires_auth,
)


@pytest.fixture(autouse=True)
def dev_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gateway_api_key", "sk-gateway-test-key")


class FakeKey:
    def __init__(self, key_id="key-1", is_active=True):
        self.id = key_id
        self.is_active = is_active
        self.last_used_at = None


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.committed = False
        self.closed = False

    async def execute(self, *args, **kwargs):
        if self._raises:
            raise self._raises
        return self._result

    async def commit(self):
        self.committed = True

    async def close(self):
        self.closed = True


def patch_session(monkeypatch, session):
    import app.middleware.auth as auth

    async def get_session():
        return session

    monkeypatch.setattr(auth, "get_session", get_session)


def check(token):
    return asyncio.run(check_api_key(token))


class ExplodingSession:
    """Any use of this session is a test failure."""

    async def execute(self, *args, **kwargs):
        raise AssertionError("the key store must not be queried")

    async def commit(self):
        raise AssertionError("the key store must not be queried")

    async def close(self):
        self.closed = True


# --- parse_bearer -------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc", "abc"),
        ("Bearer  abc", " abc"),
        ("Bearer", None),
        ("Token abc", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_bearer(header, expected):
    assert parse_bearer(header) == expected


# --- admin key ----------------------------------------------------------------


def test_valid_admin_key_does_not_touch_the_database(monkeypatch):
    patch_session(monkeypatch, ExplodingSession())

    result = check("sk-gateway-test-key")

    assert result.status is TokenStatus.VALID
    assert result.api_key_id == "gateway_admin"


def test_admin_key_still_works_when_the_database_is_down(monkeypatch):
    """The admin key is the operator's way in during a database incident."""
    patch_session(monkeypatch, None)

    assert check("sk-gateway-test-key").status is TokenStatus.VALID


def test_wrong_admin_key_is_invalid(monkeypatch):
    patch_session(monkeypatch, None)

    result = check("sk-gateway-not-the-key")

    assert result.status is TokenStatus.INVALID
    assert result.api_key_id is None


def test_empty_token_is_invalid_without_a_lookup(monkeypatch):
    patch_session(monkeypatch, ExplodingSession())

    assert check("").status is TokenStatus.INVALID


# --- database-backed keys -----------------------------------------------------


def test_valid_key_returns_its_id_and_records_use(monkeypatch):
    session = FakeSession(FakeResult(FakeKey("key-abc")))
    patch_session(monkeypatch, session)

    result = check("user-token")

    assert result.status is TokenStatus.VALID
    assert result.api_key_id == "key-abc"
    assert session.committed is True
    assert session.closed is True


def test_unknown_key_is_invalid(monkeypatch):
    patch_session(monkeypatch, FakeSession(FakeResult(None)))

    result = check("nope")

    assert result.status is TokenStatus.INVALID
    assert "Invalid" in result.detail


def test_disabled_key_is_invalid_with_a_distinct_reason(monkeypatch):
    patch_session(monkeypatch, FakeSession(FakeResult(FakeKey(is_active=False))))

    result = check("disabled-token")

    assert result.status is TokenStatus.INVALID
    assert "disabled" in result.detail.lower()


# --- store unavailability -----------------------------------------------------


def test_missing_session_is_store_unavailable_not_invalid(monkeypatch):
    """Three outcomes, not two.

    A key store that cannot be reached is not the same fact as a bad token, and
    collapsing them means either a 401 storm during an incident or a bypass.
    HTTP fails open on this; the websocket handshake fails closed.
    """
    patch_session(monkeypatch, None)

    result = check("user-token")

    assert result.status is TokenStatus.STORE_UNAVAILABLE
    assert result.ok is False


def test_a_query_error_is_store_unavailable_and_closes_the_session(monkeypatch):
    session = FakeSession(raises=RuntimeError("connection reset"))
    patch_session(monkeypatch, session)

    result = check("user-token")

    assert result.status is TokenStatus.STORE_UNAVAILABLE
    assert session.closed is True
    # The internal message is not leaked to the caller.
    assert "connection reset" not in result.detail


def test_a_raising_get_session_is_store_unavailable(monkeypatch):
    import app.middleware.auth as auth

    async def boom():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(auth, "get_session", boom)

    assert check("user-token").status is TokenStatus.STORE_UNAVAILABLE


# --- invariants that were true before the refactor ---------------------------


def test_hash_string_is_deterministic():
    assert hash_string("secret") == hash_string("secret")
    assert hash_string("a") != hash_string("b")


def test_requires_auth_still_excludes_the_websocket_path():
    """Documents why handler-level auth is required for /ws/chat.

    BaseHTTPMiddleware only runs on http scope, and this prefix list does not
    match /ws anyway. Neither fact is going to change.
    """
    assert requires_auth("/chat")
    assert requires_auth("/chat/abc")
    assert not requires_auth("/health")
    assert not requires_auth("/chat-history/sessions")
    assert not requires_auth("/ws/chat")
    assert not requires_auth("/ws")


def test_token_check_ok_property():
    assert TokenCheck(TokenStatus.VALID).ok is True
    assert TokenCheck(TokenStatus.INVALID).ok is False
    assert TokenCheck(TokenStatus.STORE_UNAVAILABLE).ok is False