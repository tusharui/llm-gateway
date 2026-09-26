import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas import StreamChunk, Usage
from app.routes import chat as chat_module


def _noop(*_args, **_kwargs):
    return None


def _frame(text: str, usage: Usage | None = None) -> StreamChunk:
    return StreamChunk(
        id="chunk-1",
        model="openai/gpt-oss-20b",
        provider="groq",
        choices=[{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        usage=usage,
    )


@pytest.fixture
def recorded(monkeypatch):
    """Capture track_usage calls instead of writing to the database."""
    calls: list[dict] = []

    async def fake_track_usage(api_key_id, data):
        calls.append({"api_key_id": api_key_id, **data})

    monkeypatch.setattr(chat_module, "track_usage", fake_track_usage)

    # The streaming path dispatches the write as a background task.
    monkeypatch.setattr(chat_module, "_record_usage_async", lambda k, d: calls.append({"api_key_id": k, **d}))
    return calls


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_module.router)
    return app


def test_stream_records_usage(recorded):
    async def fake_stream(req, preferred_provider=None):
        yield _frame("Hel")
        yield _frame("lo")
        yield _frame("", Usage(prompt_tokens=11, completion_tokens=2, total_tokens=13))

    chat_module.route_chat_stream = fake_stream
    client = TestClient(build_app())
    r = client.post("/chat", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: done" in r.text

    assert len(recorded) == 1, "streaming requests must be recorded exactly once"
    entry = recorded[0]
    assert entry["provider"] == "groq"
    assert entry["model"] == "openai/gpt-oss-20b"
    assert entry["prompt_tokens"] == 11
    assert entry["completion_tokens"] == 2
    assert entry["total_tokens"] == 13
    assert entry["success"] is True
    assert entry["cached"] is False


def test_stream_records_failure(recorded):
    async def fake_stream(req, preferred_provider=None):
        yield _frame("partial")
        raise RuntimeError("provider exploded")

    chat_module.route_chat_stream = fake_stream
    client = TestClient(build_app())
    r = client.post("/chat", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    assert "event: error" in r.text
    assert len(recorded) == 1
    assert recorded[0]["success"] is False


def test_auto_model_is_rewritten_before_dispatch(monkeypatch):
    """Providers receive a concrete model id, never the literal string "auto"."""
    seen: list[str] = []

    async def fake_stream(req, preferred_provider=None):
        seen.append(req.model)
        yield _frame("ok", Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))

    monkeypatch.setattr(chat_module, "route_chat_stream", fake_stream)
    monkeypatch.setattr(chat_module, "track_usage", _noop)
    monkeypatch.setattr(chat_module, "_record_usage_async", lambda k, d: None)

    client = TestClient(build_app())
    client.post("/chat", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    })

    assert len(seen) == 1
    assert seen[0] != "auto"
    assert "/" in seen[0] or seen[0].startswith(("gemini", "qwen", "allam", "gpt"))
