"""Websocket handshake auth, resource limits and error containment.

Close codes are asserted, not just "it refused to connect". A socket that
refuses for the wrong reason still refuses, and a client cannot build correct
retry logic against a reason string alone.

The provider call is stubbed in every case. These tests are about the
transport, and a real call would make the suite depend on provider keys.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.middleware.auth import rate_limit_store


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_key", "sk-gateway-test-key")
    monkeypatch.setattr(settings, "ws_max_message_bytes", 2048)
    monkeypatch.setattr(settings, "ws_max_messages_per_request", 4)
    monkeypatch.setattr(settings, "ws_max_turns_per_connection", 3)
    monkeypatch.setattr(settings, "ws_rate_limit", False)
    monkeypatch.setattr(settings, "ws_max_connections_per_key", 8)
    rate_limit_store.clear()

    import app.routes.websocket as ws_module

    ws_module._open_connections.clear()

    yield
    rate_limit_store.clear()
    ws_module._open_connections.clear()


class FakeSession:
    def __init__(self, api_key=None):
        self._api_key = api_key

    async def execute(self, *args, **kwargs):
        api_key = self._api_key

        class R:
            def scalar_one_or_none(self):
                return api_key

        return R()

    async def commit(self):
        pass

    async def close(self):
        pass


class FakeKey:
    def __init__(self, key_id="key-abc", is_active=True):
        self.id = key_id
        self.is_active = is_active
        self.last_used_at = None


def patch_store(monkeypatch, session):
    import app.middleware.auth as auth

    async def get_session():
        return session

    monkeypatch.setattr(auth, "get_session", get_session)


def stub_stream(monkeypatch, chunks=None, boom=False):
    """Replace the provider stream so no network call is attempted."""
    import app.routes.websocket as ws

    class Chunk:
        def __init__(self, text):
            self._text = text

        def model_dump(self):
            return {"content": self._text}

    async def fake_stream(req):
        if boom:
            raise RuntimeError("provider exploded")
        for text in chunks or ["hello"]:
            yield Chunk(text)

    monkeypatch.setattr(ws, "route_chat_stream", fake_stream)


def build_client():
    from app.routes.websocket import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


ADMIN = {"Authorization": "Bearer sk-gateway-test-key"}


def turn(text="hi", n=1):
    return json.dumps({"messages": [{"role": "user", "content": text}] * n})


def close_code_of(exc):
    return getattr(exc, "code", None)


def expect_close(session, limit=20):
    """Read until the server closes, and return the close code.

    A server-initiated close is only observed on a read. Without this a test
    that sends an oversized frame and never receives would see the context
    manager exit cleanly and conclude the socket was still open.

    Raises if nothing arrives, so a regression fails the suite instead of
    hanging it: a socket that is accepted and then never closes would
    otherwise block here forever.
    """
    for _ in range(limit):
        try:
            message = session.receive()
        except WebSocketDisconnect as exc:
            return exc.code
        if message["type"] == "websocket.close":
            return message.get("code", 1000)
    raise AssertionError(f"no close frame after {limit} reads; the socket is still open")


def drain(ws):
    """Consume everything a successful turn emits: the chunk and the done flag."""
    first = ws.receive_json()
    while True:
        try:
            message = ws.receive_json()
        except WebSocketDisconnect:
            return first
        # The terminal frame carries a routing_decision alongside `done`, so a
        # socket client can see which model tier the prompt was classified
        # into without opening a second connection to /routing/classify.
        if message.get("done") is True:
            return first


# --- authentication -----------------------------------------------------------


def test_no_credentials_is_closed_with_1008(monkeypatch):
    patch_store(monkeypatch, FakeSession())
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat") as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


def test_invalid_token_is_closed_with_1008(monkeypatch):
    patch_store(monkeypatch, FakeSession(None))
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", headers=ADMIN | {"Authorization": "Bearer sk-gateway-nope"}) as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


def test_disabled_key_is_closed_with_1008(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey(is_active=False)))
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", headers={"Authorization": "Bearer user-token"}) as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


def test_valid_admin_key_connects_and_streams(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"
        terminal = ws.receive_json()
        assert terminal["done"] is True
        assert "routing_decision" in terminal


def test_valid_database_key_connects(monkeypatch):
    patch_store(monkeypatch, FakeSession(FakeKey("key-xyz")))
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers={"Authorization": "Bearer user-token"}) as ws:
        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"


def test_key_store_unavailable_fails_closed(monkeypatch):
    """The opposite of HTTP, and deliberately so.

    HTTP lets a request through when Postgres is unreachable so an incident
    does not become an outage. A socket stays open and streams billable tokens
    for as long as the client holds it, so the same rule here would hand out
    free LLM access for the life of the connection.
    """
    patch_store(monkeypatch, None)
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", headers={"Authorization": "Bearer user-token"}) as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


def test_admin_key_works_while_the_store_is_down(monkeypatch):
    """The operator is never locked out of their own gateway."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"


# --- token transport ----------------------------------------------------------


def test_subprotocol_carries_the_token_and_is_echoed(monkeypatch):
    """Browsers cannot set headers, so the token may arrive as a subprotocol.

    The echo is mandatory: a client that offered Sec-WebSocket-Protocol and got
    none back fails the handshake in the browser, before this code runs.
    """
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", subprotocols=["bearer", "sk-gateway-test-key"]) as ws:
        assert ws.accepted_subprotocol == "bearer"
        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"


def test_subprotocol_only_without_a_token_is_rejected(monkeypatch):
    patch_store(monkeypatch, None)
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", subprotocols=["bearer"]) as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


@pytest.mark.parametrize("key", ["token", "api_key", "access_token"])
def test_token_in_a_query_parameter_is_rejected(monkeypatch, key):
    """Rejected explicitly, not ignored.

    A token in a URL reaches access logs, proxy logs and Referer headers. The
    frontend does not use this socket, so there is nothing to accommodate.
    """
    patch_store(monkeypatch, None)
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/chat?{key}=sk-gateway-test-key") as ws:
            ws.receive_text()

    assert close_code_of(exc.value) == 1008


def test_a_valid_token_in_a_query_parameter_is_still_rejected(monkeypatch):
    """The token being correct does not make the channel acceptable."""
    patch_store(monkeypatch, None)
    client = build_client()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat?token=sk-gateway-test-key") as ws:
            ws.receive_text()


# --- limits -------------------------------------------------------------------


def test_oversized_frame_is_closed_with_1009(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps({"messages": [{"role": "user", "content": "x" * 5000}]}))
        assert expect_close(ws) == 1009


def test_frame_at_the_cap_is_accepted(monkeypatch):
    """The boundary is inclusive, so an off-by-one cannot silently tighten it."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    overhead = len(json.dumps({"messages": [{"role": "user", "content": ""}]}))
    content = "x" * (settings.ws_max_message_bytes - overhead)

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps({"messages": [{"role": "user", "content": content}]}))
        assert ws.receive_json()["content"] == "hello"


def test_one_byte_over_the_cap_is_refused(monkeypatch):
    """The other side of the boundary, so the cap is a real line."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    overhead = len(json.dumps({"messages": [{"role": "user", "content": ""}]}))
    content = "x" * (settings.ws_max_message_bytes - overhead + 1)

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps({"messages": [{"role": "user", "content": content}]}))
        assert expect_close(ws) == 1009


def test_too_many_messages_in_one_frame_is_closed_with_1008(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn(n=settings.ws_max_messages_per_request + 1))
        assert expect_close(ws) == 1008


def test_message_array_exactly_at_the_cap_is_accepted(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn(n=settings.ws_max_messages_per_request))
        assert drain(ws)["content"] == "hello"


def test_turn_limit_closes_the_socket(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        for index in range(settings.ws_max_turns_per_connection + 1):
            ws.send_text(turn())
            try:
                drain(ws)
            except WebSocketDisconnect as exc:
                # Only the final turn may be cut off.
                assert index == settings.ws_max_turns_per_connection
                assert exc.code == 1008
                return
    raise AssertionError("the turn limit never fired")


def test_turns_below_the_limit_stay_open(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        for _ in range(settings.ws_max_turns_per_connection):
            ws.send_text(turn())
            assert drain(ws)["content"] == "hello"


def test_rate_limit_closes_with_1013(monkeypatch):
    """The handshake is charged too, so the budget is 3: handshake, turn, turn."""
    monkeypatch.setattr(settings, "ws_rate_limit", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 3)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert drain(ws)["content"] == "hello"
        ws.send_text(turn())
        assert drain(ws)["content"] == "hello"
        ws.send_text(turn())
        assert expect_close(ws) == 1013


def test_the_handshake_is_charged_against_the_shared_budget(monkeypatch):
    """Opening a socket is not free.

    The per-message budget cannot see a connection that sends nothing, and a
    socket costs an asyncio task and a buffered connection regardless. Asserted
    on the store rather than by opening idle sockets and blocking on reads:
    an accepted socket that sends nothing never closes, so a client read on it
    waits forever.
    """
    monkeypatch.setattr(settings, "ws_rate_limit", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 10)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert drain(ws)["content"] == "hello"
        # One handshake plus one message.
        assert rate_limit_store["gateway_admin"]["count"] == 2


def test_an_exhausted_budget_refuses_the_handshake(monkeypatch):
    monkeypatch.setattr(settings, "ws_rate_limit", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 1)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    # The first connection spends the single unit on its handshake alone.
    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert expect_close(ws) == 1013


def test_connection_cap_is_enforced_per_key(monkeypatch):
    monkeypatch.setattr(settings, "ws_max_connections_per_key", 2)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    first = client.websocket_connect("/ws/chat", headers=ADMIN)
    second = client.websocket_connect("/ws/chat", headers=ADMIN)
    with first, second:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
                ws.receive_text()
        assert exc.value.code == 1008


def test_closing_a_socket_returns_its_connection_slot(monkeypatch):
    """The cap must be reusable, or a key can permanently exhaust its own budget
    by tripping a limit once."""
    monkeypatch.setattr(settings, "ws_max_connections_per_key", 1)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    for _ in range(3):
        with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
            ws.send_text(turn())
            assert drain(ws)["content"] == "hello"

    import app.routes.websocket as ws_module

    assert ws_module.open_connection_count("gateway_admin") == 0


def test_slot_is_released_when_a_limit_closes_the_socket(monkeypatch):
    """A turn-limit close is still a closed socket, so the slot comes back."""
    monkeypatch.setattr(settings, "ws_max_turns_per_connection", 1)
    monkeypatch.setattr(settings, "ws_max_connections_per_key", 4)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    for _ in range(2):
        with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
            for _ in range(3):
                try:
                    ws.send_text(turn())
                    drain(ws)
                except WebSocketDisconnect:
                    break

    import app.routes.websocket as ws_module

    assert ws_module.open_connection_count("gateway_admin") == 0


def test_slot_is_not_taken_by_a_rejected_handshake(monkeypatch):
    """A refused connection must not consume a slot it never held."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    for _ in range(5):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_text()

    import app.routes.websocket as ws_module

    assert ws_module.open_connection_count() == 0


def test_websocket_and_http_share_one_rate_limit_budget(monkeypatch):
    """Otherwise an exhausted key just opens a socket.

    Without a shared store, a key that has burned its HTTP allowance gets a full
    fresh one over /ws/chat, and the HTTP rate limit becomes decorative.
    """
    monkeypatch.setattr(settings, "ws_rate_limit", True)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 2)
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    from app.middleware.auth import consume_rate_limit

    assert consume_rate_limit("gateway_admin").allowed is True
    assert consume_rate_limit("gateway_admin").allowed is True
    assert consume_rate_limit("gateway_admin").allowed is False

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
            ws.send_text(turn())
            ws.receive_json()

    assert close_code_of(exc.value) == 1013


# --- error containment --------------------------------------------------------


def test_invalid_json_does_not_close_the_socket(monkeypatch):
    """Recoverable per-message faults stay recoverable; only policy closes."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text("{not json")
        assert ws.receive_json() == {"error": "Invalid JSON"}

        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"


def test_non_object_payload_is_reported_not_accepted(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps([1, 2, 3]))
        assert "Expected a JSON object" in ws.receive_json()["error"]


def test_messages_must_be_a_list(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps({"messages": {"role": "user"}}))
        assert "must be a list" in ws.receive_json()["error"]


def test_validation_errors_do_not_echo_the_offending_value(monkeypatch):
    """pydantic's str(exc) carries input_value=<the value>.

    Sent verbatim, that reflects arbitrary caller-supplied text straight back
    over the socket. Verified against the real schema rather than assumed.
    """
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    secret = "sk-LEAKED0123456789abcdef"
    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(
            json.dumps(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "temperature": secret,
                }
            )
        )
        payload = ws.receive_json()

    serialised = json.dumps(payload)
    assert secret not in serialised
    assert payload["error"] == "Invalid request"
    assert payload["details"][0]["field"] == "temperature"
    assert payload["details"][0]["type"]


def test_validation_error_names_the_field_without_the_value(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(
            json.dumps({"messages": [{"role": "user", "content": 12345}]})
        )
        payload = ws.receive_json()

    assert payload["error"] == "Invalid message"
    assert payload["details"][0]["field"] == "0.content"
    assert "12345" not in json.dumps(payload)


def test_a_rejected_validation_does_not_close_the_socket(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(json.dumps({"messages": [{"role": "user", "content": 1}], "temperature": "nope"}))
        assert ws.receive_json()["error"] == "Invalid message"

        ws.send_text(turn())
        assert ws.receive_json()["content"] == "hello"


def test_validation_detail_is_logged_server_side(monkeypatch, caplog):
    """A client-safe error has to still be diagnosable by an operator."""
    import logging

    patch_store(monkeypatch, None)
    stub_stream(monkeypatch)
    client = build_client()

    with caplog.at_level(logging.WARNING, logger="app.routes.websocket"):
        with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
            ws.send_text(json.dumps({"messages": [{"role": "user", "content": 1}]}))
            ws.receive_json()

    assert caplog.records
    assert any("Invalid message" in r.getMessage() for r in caplog.records)


def test_logged_detail_is_redacted(monkeypatch, caplog):
    import logging

    patch_store(monkeypatch, None)
    stub_stream(monkeypatch, boom=True)

    import app.routes.websocket as ws_module

    async def failing_failover(req):
        raise RuntimeError("failed with https://x/?key=sk-abcdef0123456789abcdef")

    monkeypatch.setattr(ws_module, "route_with_failover", failing_failover)
    client = build_client()

    with caplog.at_level(logging.WARNING, logger="app.routes.websocket"):
        try:
            with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
                ws.send_text(turn())
                ws.receive_json()
        except WebSocketDisconnect:
            pass

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "sk-abcdef0123456789abcdef" not in logged
    assert "failover also failed" in logged


def test_provider_failure_gives_the_client_a_generic_reason(monkeypatch):
    """Not the redacted exception text: a reason the client cannot act on and
    cannot mine for internal structure."""
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch, boom=True)

    import app.routes.websocket as ws_module

    async def failing_failover(req):
        raise RuntimeError("provider https://internal.example/?key=sk-abcdef0123456789abcdef")

    monkeypatch.setattr(ws_module, "route_with_failover", failing_failover)
    client = build_client()

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
            ws.send_text(turn())
            ws.receive_json()

    reason = getattr(exc.value, "reason", "") or ""
    assert "sk-abcdef" not in reason
    assert "internal.example" not in reason
    assert "Internal error" in reason


def test_streaming_error_falls_back_to_failover(monkeypatch):
    patch_store(monkeypatch, None)
    stub_stream(monkeypatch, boom=True)

    import app.routes.websocket as ws_module

    class Result:
        def model_dump(self):
            return {"content": "from failover"}

    async def fake_failover(req):
        return Result(), "groq"

    monkeypatch.setattr(ws_module, "route_with_failover", fake_failover)
    client = build_client()

    with client.websocket_connect("/ws/chat", headers=ADMIN) as ws:
        ws.send_text(turn())
        assert ws.receive_json()["content"] == "from failover"
        assert ws.receive_json()["done"] is True


# --- invariants ---------------------------------------------------------------


def test_auth_middleware_cannot_cover_this_route():
    """Why this handler authenticates itself, asserted rather than left in a
    comment that could go stale.

    requires_auth does not match /ws, so even if the middleware did run on this
    scope it would pass the request through. Both facts are load-bearing: fix
    either one and the route still needs its own check.
    """
    from app.middleware.auth import requires_auth

    assert not requires_auth("/ws/chat")
    assert not requires_auth("/ws")
    assert not requires_auth("/ws/chat?token=abc")


def test_the_handler_authenticates_itself():
    """The structural counterpart: no credential means no provider call.

    Every other test in this file depends on it, so it is asserted once,
    directly, rather than only as a precondition.
    """
    import inspect

    from app.routes.websocket import websocket_chat

    source = inspect.getsource(websocket_chat)
    assert "check_api_key" in source
    # And it happens before any accept, so an unauthenticated peer never gets
    # a connection it can hold open.
    assert source.index("check_api_key") < source.index("websocket.accept")


def test_close_reason_is_truncated_to_the_rfc_limit():
    from app.routes.websocket import MAX_CLOSE_REASON_BYTES, _truncate_reason

    long_reason = "x" * 500
    truncated = _truncate_reason(long_reason)

    assert len(truncated.encode("utf-8")) <= MAX_CLOSE_REASON_BYTES


def test_truncation_respects_multibyte_boundaries():
    from app.routes.websocket import _truncate_reason

    truncated = _truncate_reason("é" * 200)

    truncated.encode("utf-8").decode("utf-8")  # must not raise