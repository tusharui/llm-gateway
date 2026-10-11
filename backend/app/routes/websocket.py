"""Websocket chat transport.

Authentication and resource bounds are enforced **in the handler**, not by
AuthMiddleware, because BaseHTTPMiddleware only runs on ``http`` scope. It never
sees a websocket connection. ``requires_auth("/ws/chat")`` is also false: the
prefix list is /chat, /embeddings and /batch. Both facts are covered by tests,
because either could change and silently reopen this route.

## Credentials

Browsers cannot set headers on a websocket handshake, so two sources are
accepted:

  ``Authorization: Bearer <token>``      server-side clients, and the tests
  ``Sec-WebSocket-Protocol: bearer, <t>`` browsers

Query parameters are **rejected**, not merely undocumented. A token in the URL
lands in access logs, proxy logs, and Referer headers, and it survives in
browser history. Since the frontend does not use this socket yet, there is
nothing to break.

When the client offers a subprotocol the server must echo one back or the
browser fails the handshake outright, so ``accept(subprotocol="bearer")`` is
mandatory and not cosmetic.

## Close codes

  1008 policy violation   auth failure, message-array cap, turn cap
  1009 message too big    a frame over WS_MAX_MESSAGE_BYTES
  1013 try again later    the shared HTTP rate-limit budget is exhausted
  1011 internal error     a fault the client cannot act on

1008 is the right code for authentication because it means "this will not
change on retry". The limits use the codes that actually describe them, so a
client can tell "your token is wrong" from "your frame is too big" from "back
off" without parsing a reason string.

## Why this fails closed when the key store is down

``check_api_key`` returns STORE_UNAVAILABLE when the database cannot be
consulted. Both transports now answer that the same way -- closed -- but for
different reasons worth keeping separate.

For a socket the argument is about duration: the connection stays open,
streaming billable tokens, for as long as the client holds it, so accepting
one during a database blip converts a transient failure into indefinite
access.

For an HTTP request the argument is about cost rather than duration, and it
is the stronger of the two. Accepting a token the store could not verify means
accepting a string an attacker chose: a typo in DATABASE_URL or thirty
seconds of database trouble would otherwise hand anyone who typed a random
value into an Authorization header the owner's provider spend. The earlier
fail-open behaviour is recorded in ``AuthMiddleware``, where it was removed.

The admin key (``sk-gateway-`` prefix) is resolved without touching the
database, so an operator is never locked out by either decision.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config import settings
from app.engine.failover import route_with_failover
from app.engine.router import route_chat_stream
from app.middleware.auth import (
    ADMIN_KEY_ID,
    TokenStatus,
    check_api_key,
    consume_rate_limit,
    parse_bearer,
)
from app.providers.registry import get_available_providers
from app.redact import redact_error
from app.routes.chat import resolve_route
from app.schemas import ChatMessage, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()

CLOSE_NORMAL = 1000
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_INTERNAL_ERROR = 1011
CLOSE_TRY_AGAIN_LATER = 1013

# RFC 6455 caps a close reason at 123 bytes. A longer one is a protocol error
# and some clients drop the frame.
MAX_CLOSE_REASON_BYTES = 123

WS_SUBPROTOCOL = "bearer"
_FORBIDDEN_QUERY_KEYS = ("token", "api_key", "apikey", "access_token")

# api_key_id -> open connection count. In-process and therefore per worker,
# like rate_limit_store: N workers means N times the cap. Moving either to
# shared storage is the same problem and the same fix, and neither is worth
# doing before there is more than one worker.
_open_connections: dict[str, int] = {}


def _acquire_connection_slot(api_key_id: str) -> bool:
    """Reserve one of this key's connection slots. False when full."""
    current = _open_connections.get(api_key_id, 0)
    if current >= settings.ws_max_connections_per_key:
        return False
    _open_connections[api_key_id] = current + 1
    return True


def _release_connection_slot(api_key_id: str) -> None:
    remaining = _open_connections.get(api_key_id, 1) - 1
    if remaining <= 0:
        _open_connections.pop(api_key_id, None)
    else:
        _open_connections[api_key_id] = remaining


def open_connection_count(api_key_id: str | None = None) -> int:
    """Currently open sockets, for one key or in total. Used by the tests."""
    if api_key_id is None:
        return sum(_open_connections.values())
    return _open_connections.get(api_key_id, 0)


def _truncate_reason(reason: str) -> str:
    encoded = reason.encode("utf-8")[:MAX_CLOSE_REASON_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def extract_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Return ``(token, subprotocol_to_echo)`` from the handshake.

    The subprotocol must be echoed whenever the client offered one, or the
    browser refuses the connection before the application sees it.
    """
    token = parse_bearer(websocket.headers.get("authorization"))
    if token:
        return token, None

    offered = websocket.headers.get("sec-websocket-protocol")
    if offered:
        for candidate in (part.strip() for part in offered.split(",")):
            if candidate and candidate != WS_SUBPROTOCOL:
                return candidate, WS_SUBPROTOCOL
    return None, None


async def _reject(websocket: WebSocket, code: int, reason: str, subprotocol: str | None = None) -> None:
    """Close with a specific code rather than a bare handshake failure.

    Starlette turns a ``close`` before ``accept`` into an HTTP 403 denial, which
    the client cannot distinguish from any other refused connection. Accepting
    first and closing immediately sends a real close frame carrying the code,
    so the client can tell an expired key from a frame that was too big.
    Nothing is read and no provider is called, so the accepted-then-closed
    moment costs nothing.
    """
    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept(subprotocol=subprotocol)
    await websocket.close(code=code, reason=_truncate_reason(reason))


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    token, subprotocol = extract_token(websocket)

    if not token:
        await _reject(
            websocket,
            CLOSE_POLICY_VIOLATION,
            "Authentication required: send Authorization: Bearer <token> or "
            "Sec-WebSocket-Protocol: bearer, <token>",
            subprotocol,
        )
        return

    if any(key in websocket.query_params for key in _FORBIDDEN_QUERY_KEYS):
        # Refused explicitly rather than ignored, so the client finds out why
        # instead of silently getting an unauthenticated socket.
        await _reject(
            websocket,
            CLOSE_POLICY_VIOLATION,
            "Token in a query parameter is rejected: URLs are logged. "
            "Send it as a header instead.",
            subprotocol,
        )
        return

    check = await check_api_key(token)
    if check.status is not TokenStatus.VALID:
        reason = (
            "Authentication unavailable while the key store is unreachable. "
            "Retry shortly."
            if check.status is TokenStatus.STORE_UNAVAILABLE
            else check.detail or "Invalid API key"
        )
        await _reject(websocket, CLOSE_POLICY_VIOLATION, reason, subprotocol)
        return

    api_key_id = check.api_key_id or ADMIN_KEY_ID

    if settings.ws_rate_limit:
        # Charged at the handshake as well as per message. Without this a valid
        # key can open sockets without ever sending anything, and a per-message
        # budget never notices: the cost is an asyncio task and a buffered
        # socket per connection, and none of it is metered.
        handshake_verdict = consume_rate_limit(api_key_id)
        if not handshake_verdict.allowed:
            await _reject(
                websocket,
                CLOSE_TRY_AGAIN_LATER,
                f"Rate limit exceeded. Retry after {handshake_verdict.retry_after_seconds}s",
                subprotocol,
            )
            return

    if not _acquire_connection_slot(api_key_id):
        await _reject(
            websocket,
            CLOSE_POLICY_VIOLATION,
            f"Too many open connections for this key "
            f"(limit {settings.ws_max_connections_per_key}). Close one first.",
            subprotocol,
        )
        return

    await websocket.accept(subprotocol=subprotocol)

    turns = 0
    try:
        while True:
            if websocket.client_state is not WebSocketState.CONNECTED:
                break

            try:
                raw = await websocket.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                break

            if len(raw.encode("utf-8")) > settings.ws_max_message_bytes:
                await _reject(
                    websocket,
                    CLOSE_MESSAGE_TOO_BIG,
                    f"Frame exceeds {settings.ws_max_message_bytes} bytes",
                )
                return

            if settings.ws_rate_limit:
                verdict = consume_rate_limit(api_key_id)
                if not verdict.allowed:
                    await _reject(
                        websocket,
                        CLOSE_TRY_AGAIN_LATER,
                        f"Rate limit exceeded. Retry after {verdict.retry_after_seconds}s",
                    )
                    return

            turns += 1
            if turns > settings.ws_max_turns_per_connection:
                await _reject(
                    websocket,
                    CLOSE_POLICY_VIOLATION,
                    f"Turn limit of {settings.ws_max_turns_per_connection} reached. "
                    "Reconnect to continue.",
                )
                return

            await _handle_turn(websocket, raw)

    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - a fault must close cleanly, not traceback
        # The client gets a generic reason. The detail goes to the server log
        # through redact_error, so it is diagnosable without reflecting an
        # exception message to whoever happens to hold the socket.
        logger.error("websocket turn failed for api_key_id=%s: %s", api_key_id, redact_error(exc))
        try:
            await websocket.close(
                code=CLOSE_INTERNAL_ERROR,
                reason="Internal error. Check the server logs.",
            )
        except RuntimeError:
            pass
        return
    finally:
        # Every exit from here is a return, a break or an exception, and each of
        # them ends the socket. Leaking the slot on any one of them would let a
        # key exhaust its own connection budget permanently by tripping a limit.
        _release_connection_slot(api_key_id)


def _parse_turn(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _validation_error(prefix: str, exc: Exception, *, index_offset: int | None = None) -> dict[str, Any]:
    """A client-safe validation error.

    pydantic's ``str(exc)`` includes ``input_value=<the offending value>``, so
    sending it verbatim reflects arbitrary caller-supplied text straight back
    over the socket. A client that puts a credential in ``temperature`` gets
    that credential echoed to it, and into whatever it logs.

    Only the field path, the error type and the static message template are
    returned. ``msg`` is a fixed string like "Input should be a valid float";
    it never contains the input. The full detail is logged server-side through
    ``redact_error`` so the failure is still diagnosable.

    ``index_offset`` prefixes the field path with the array position, so a
    client with four messages is told *which* one is wrong rather than being
    left to guess.
    """
    from pydantic import ValidationError

    errors: list[dict[str, Any]] = []
    if isinstance(exc, ValidationError):
        for item in exc.errors():
            loc = [str(part) for part in item.get("loc", ())]
            if index_offset is not None:
                loc = [str(index_offset), *loc]
            errors.append(
                {"field": ".".join(loc), "type": item.get("type"), "message": item.get("msg")}
            )
    if not errors:
        # A non-pydantic failure reached the same branch. Redact it rather than
        # trusting that it happens to be safe.
        errors = [{"field": "", "type": type(exc).__name__, "message": redact_error(exc)}]

    logger.warning(
        "%s rejected: %s | %s",
        prefix,
        json.dumps(errors, ensure_ascii=False),
        redact_error(exc),
    )
    return {"error": prefix, "details": errors}


def _build_messages(raw_messages: list[Any]) -> list[ChatMessage]:
    """Validate each message so a failure names its position.

    Validating the list in one comprehension produces a ValidationError whose
    ``loc`` is relative to the individual model, so a client with four messages
    is told "content" and has to find the offending one itself.
    """
    from pydantic import ValidationError

    messages: list[ChatMessage] = []
    for index, raw in enumerate(raw_messages):
        try:
            messages.append(ChatMessage(**raw))
        except ValidationError as exc:
            raise _IndexedValidationError(exc, index) from exc
        except (TypeError, ValueError) as exc:
            raise _IndexedValidationError(exc, index) from exc
    return messages


class _IndexedValidationError(Exception):
    """Carries the offending array index alongside the validation failure."""

    def __init__(self, cause: Exception, index: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.index = index


async def _handle_turn(websocket: WebSocket, raw: str) -> None:
    try:
        data = _parse_turn(raw)
    except json.JSONDecodeError:
        await websocket.send_json({"error": "Invalid JSON"})
        return
    except ValueError as exc:
        await websocket.send_json({"error": str(exc)})
        return

    raw_messages = data.get("messages", [])
    if not isinstance(raw_messages, list):
        await websocket.send_json({"error": "'messages' must be a list"})
        return

    if len(raw_messages) > settings.ws_max_messages_per_request:
        # Not recoverable by sending a smaller frame on the same socket: the
        # cap exists to bound what one frame can ask for, so the connection is
        # the unit that has to go.
        await _reject(
            websocket,
            CLOSE_POLICY_VIOLATION,
            f"Frame carries {len(raw_messages)} messages, limit is "
            f"{settings.ws_max_messages_per_request}",
        )
        return

    if not raw_messages:
        await websocket.send_json({"error": "No messages provided"})
        return

    try:
        messages = _build_messages(raw_messages)
    except _IndexedValidationError as exc:
        await websocket.send_json(
            _validation_error("Invalid message", exc.cause, index_offset=exc.index)
        )
        return
    except Exception as exc:  # noqa: BLE001 - projected into a safe shape below
        await websocket.send_json(_validation_error("Invalid message", exc))
        return

    try:
        req = ChatRequest(
            model=data.get("model", "auto"),
            messages=messages,
            stream=True,
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens"),
            top_p=data.get("top_p"),
        )
    except Exception as exc:  # noqa: BLE001 - projected into a safe shape below
        await websocket.send_json(_validation_error("Invalid request", exc))
        return

    # Route before dispatch, exactly as the HTTP path does.
    #
    # The provider adapters send req.model verbatim, so "auto" reaches the
    # provider as the literal string "auto" and 404s. routes/chat.py rewrites
    # it with an explicit comment about exactly this; this handler did not,
    # so every default-configuration socket turn failed against the upstream
    # API. The fix is not to duplicate the routing logic but to call the same
    # helper the HTTP path calls, so the two transports cannot disagree about
    # which model a prompt gets.
    routing_reasoning: str | None = None
    try:
        available = get_available_providers()
        provider_name, model_id, routing_reasoning = resolve_route(req, available)
        req.model = model_id
    except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
        await websocket.send_json(_validation_error("Cannot route request", exc))
        return

    try:
        async for chunk in route_chat_stream(req):
            await websocket.send_json(chunk.model_dump())
    except Exception as exc:  # noqa: BLE001 - the stream fails, then failover runs
        logger.warning("stream failed, attempting failover: %s", redact_error(exc))
        try:
            result, _provider = await route_with_failover(req)
            await websocket.send_json(result.model_dump())
        except Exception as failover_exc:  # noqa: BLE001 - reported, not swallowed
            logger.error("failover also failed: %s", redact_error(failover_exc))
            raise

    await websocket.send_json({"done": True, "routing_decision": routing_reasoning})