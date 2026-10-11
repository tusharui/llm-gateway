"""One error envelope, whatever went wrong.

The gateway returned three incompatible shapes:

    {"error": {...}}      unhandled exceptions
    {"detail": "..."}     HTTPException
    {"detail": [{...}]}   FastAPI request validation

A client could not write one error path, because "is it under `error` or
`detail`?" depended on how the server happened to fail. Everything is now
under `error`, shaped like the OpenAI chat-completions error object, since
that is the API this gateway emulates.

The 422 case needed an app-level exception handler: FastAPI registers its own
for RequestValidationError and runs it inside the middleware stack, so it never
reached ErrorHandlerMiddleware and kept the old shape.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.middleware.error_handler import (
    ErrorHandlerMiddleware,
    _strip_echoed_input,
    error_body,
    http_exception_handler,
    validation_exception_handler,
)
from app.middleware.logging import LoggingMiddleware
from app.schemas import ChatRequest

ADMIN = "sk-gateway-admin-key"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(settings, "gateway_api_key", ADMIN)


class WeirdModel(ChatRequest):
    pass


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(FastAPIHTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(LoggingMiddleware)

    @app.post("/validated")
    async def validated(payload: ChatRequest, request: Request):
        return {"ok": True}

    @app.post("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    @app.post("/teapot")
    async def teapot():
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="I am a teapot")

    return app


@pytest.fixture
def client():
    return TestClient(build_app(), raise_server_exceptions=False)


def test_unhandled_exception_uses_the_error_envelope(client):
    r = client.post("/boom")
    assert r.status_code == 500
    body = r.json()
    assert "detail" not in body
    assert body["error"]["type"] == "INTERNAL_ERROR"
    assert "kaboom" in body["error"]["message"]


def test_http_exception_uses_the_error_envelope(client):
    r = client.post("/teapot")
    assert r.status_code == 418
    body = r.json()
    assert "detail" not in body
    assert body["error"]["message"] == "I am a teapot"
    assert body["error"]["type"] == "HTTP_418"


def test_validation_error_uses_the_error_envelope(client):
    r = client.post("/validated", json={"model": "", "messages": []})
    assert r.status_code == 422
    body = r.json()
    assert "detail" not in body, "validation errors kept the old envelope"
    assert body["error"]["type"] == "INVALID_REQUEST"
    assert body["error"]["message"], "message must be a string for OpenAI-shaped clients"
    assert body["error"]["details"], "per-field detail must survive"


def test_every_error_path_shares_one_shape(client):
    """The actual invariant: same top-level key, whatever the status."""
    for response in (
        client.post("/boom"),
        client.post("/teapot"),
        client.post("/validated", json={"model": "", "messages": []}),
    ):
        body = response.json()
        assert set(body) == {"error"}, f"unexpected envelope: {body}"
        assert {
            "message",
            "type",
            "param",
            "code",
            "request_id",
        } <= set(body["error"])


def test_validation_does_not_echo_the_callers_input(client):
    """Pydantic includes the offending value; it must not be reflected back.

    The value is the caller's own and they already have it. Echoing it means
    whatever sits in a rejected field lands in the response body and in
    whatever logs that body.

    The payload is a credential-shaped string in a field with a strict
    constraint (temperature must be a float in [0, 2]), so validation fails
    and the value would otherwise appear under `input`.
    """
    secret = "sk-not-a-real-key-but-still-a-credential-shape"
    r = client.post(
        "/validated",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "x"}],
            "temperature": secret,
        },
    )

    assert r.status_code == 422, "payload should have failed validation"
    assert secret not in r.text, "the rejected input was reflected in the response"


def test_strip_echoed_input_preserves_the_useful_fields():
    details = [
        {
            "type": "string_too_short",
            "loc": ["body", "model"],
            "msg": "String should have at least 1 character",
            "input": "secret-value",
            "ctx": {"min_length": 1},
        }
    ]
    cleaned = _strip_echoed_input(details)
    assert cleaned[0]["loc"] == ["body", "model"]
    assert cleaned[0]["msg"]
    assert "input" not in cleaned[0]
    assert "ctx" not in cleaned[0]


def test_request_id_is_present_in_the_body_and_the_header(client):
    r = client.post("/boom", headers={"X-Request-ID": "trace-abc-123"})

    assert r.headers.get("x-request-id") == "trace-abc-123"
    assert r.json()["error"]["request_id"] == "trace-abc-123"


def test_unmatched_route_uses_the_error_envelope(client):
    """A 404 comes from Starlette's router, not FastAPI.

    It is a different exception class from the one raised inside a route, so
    it kept `{"detail": "Not Found"}` even after the FastAPI handler was
    registered. Both are registered on the app in main.py.
    """
    r = client.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert set(r.json()) == {"error"}
    assert r.json()["error"]["type"] == "NOT_FOUND"


def test_error_type_maps_status_to_a_meaningful_class(client):
    """A client should branch on `type`, not on the status number or a message."""
    from app.middleware.error_handler import _ERROR_TYPE_BY_STATUS

    assert _ERROR_TYPE_BY_STATUS[401] == "AUTHENTICATION_ERROR"
    assert _ERROR_TYPE_BY_STATUS[403] == "PERMISSION_ERROR"
    assert _ERROR_TYPE_BY_STATUS[429] == "RATE_LIMIT_ERROR"
    assert _ERROR_TYPE_BY_STATUS[503] == "SERVICE_UNAVAILABLE"
    assert _ERROR_TYPE_BY_STATUS[422] == "INVALID_REQUEST"


def test_error_body_helper_omits_details_when_absent():
    body = error_body(
        status_code=400, message="m", error_type="INVALID_REQUEST", request_id=None
    )
    assert "details" not in body["error"]


def test_validation_message_is_a_string_even_with_many_errors():
    """`message` must stay a string when a client has three failures at once."""
    from app.middleware.error_handler import _summarise_validation

    details = [{"loc": ["body", f"f{i}"], "msg": f"bad {i}"} for i in range(6)]
    summary = _summarise_validation(details)

    assert isinstance(summary, str)
    assert "+3 more" in summary


def test_validation_message_handles_an_empty_list():
    from app.middleware.error_handler import _summarise_validation

    assert isinstance(_summarise_validation([]), str)
    assert isinstance(_summarise_validation(None), str)