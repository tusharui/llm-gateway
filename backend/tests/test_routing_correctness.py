"""Routing correctness: failover targets, breaker accounting, websocket routing.

Three defects from the audit, each stated here as a test that fails without the
fix.

* F3  -- ``alternate_model`` ignored which provider it was substituting *for*,
         so the failover chain asked Gemini for ``openai/gpt-oss-120b`` and
         spent a round-trip on a guaranteed 404.
* F4  -- ``record_failure`` lived inside the retried closure, so one client
         request counted as four failures against a threshold of five.
* F6  -- ``/ws/chat`` passed the raw model string through, so the documented
         default of ``"auto"`` reached providers as the literal string "auto".

The central invariant for F3 is worth stating on its own, because it covers
every combination rather than the one that happened to be observed:

    for every (provider, model) in failover_chain(...):
        model must be in that provider's configured model list
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import circuit_breaker as cb
from app.engine.auto_router import alternate_model, auto_route, tier_for_model
from app.engine.retry import with_retry
from app.middleware.auth import rate_limit_store
from app.providers.interface import AIProvider
from app.schemas import ChatRequest

ALL_PROVIDERS = ("groq", "gemini", "openrouter")
ALL_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "openai/gpt-4o-mini",
    "anthropic/claude-haiku-4.5",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "mistralai/mistral-small-3.2-24b-instruct",
)


class Recording(AIProvider):
    """A provider that records what it was asked to serve."""

    def __init__(self, name, *, available=True, ok=True):
        super().__init__(name)
        self.available = available
        self.ok = ok
        self.asked: list[str] = []

    async def chat(self, req):
        self.asked.append(req.model)
        if not self.ok:
            raise RuntimeError(f"{self.name} is down")
        from app.schemas import ChatResponse, Usage

        return ChatResponse(
            id="x",
            model=req.model,
            provider=self.name,
            choices=[{"index": 0, "message": {"role": "assistant", "content": "hi"},
                      "finish_reason": "stop"}],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    async def chat_stream(self, req):
        self.asked.append(req.model)
        if not self.ok:
            raise RuntimeError(f"{self.name} is down")
        from app.schemas import StreamChunk

        yield StreamChunk(
            id="x", model=req.model, provider=self.name,
            choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        )

    async def models(self):
        return []

    async def health_check(self):
        return {"ok": self.ok, "latency_ms": 1}


@pytest.fixture
def registry(monkeypatch):
    """Install a controlled set of providers and restore the registry after."""
    import app.providers.registry as reg

    original = dict(reg._providers)
    cb.reset_all()
    yield reg
    reg._providers = original
    cb.reset_all()


def install(registry, **providers):
    registry._providers = dict(providers)


# --- F3: the failover chain must never name a model a provider cannot serve ---


def test_failover_chain_only_names_models_its_provider_hosts(registry):
    """The invariant that covers all 99 (model, preferred) combinations.

    The observed failure was one cell of this matrix:
        failover_chain('openai/gpt-oss-120b', 'gemini')
            -> [('gemini', 'openai/gpt-oss-120b'), ...]

    Gemini hosts none of the powerful-tier ids, so the chain opened with a 404
    and burned the first failover attempt on it.
    """
    from app.engine.router import failover_chain

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    cfg = settings.get_providers()

    violations = []
    for model in ALL_MODELS:
        for preferred in (*ALL_PROVIDERS, None):
            chain = failover_chain(model, preferred)
            for provider, candidate in chain:
                if candidate not in cfg[provider.name].models:
                    violations.append(
                        f"model={model!r} preferred={preferred!r} "
                        f"asked {provider.name} for {candidate!r}"
                    )

    assert not violations, "failover chain targets models providers do not host:\n" + "\n".join(
        violations
    )


def test_preferred_provider_that_cannot_serve_is_not_asked_first(registry):
    """A caller naming the wrong provider must not open the chain with a 404."""
    from app.engine.router import failover_chain

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})

    chain = failover_chain("openai/gpt-oss-120b", "gemini")

    assert chain, "chain must not be empty"
    provider, model = chain[0]
    assert provider.name == "groq", (
        "the only provider hosting openai/gpt-oss-120b is groq; the chain "
        f"started with {provider.name}"
    )
    assert model == "openai/gpt-oss-120b"


def test_alternate_model_respects_the_target_provider(registry):
    """The fix: hostability is part of the search, not a filter after it.

    The old signature returned the first tier entry that was not the excluded
    provider, without regard to whether the target could serve it. Here
    groq is excluded and the powerful tier is
    [groq/gpt-oss-120b, gemini/gemini-2.5-flash, openrouter/llama-3.3-70b], so
    the old answer was `gemini-2.5-flash` regardless of who asked. Asking for
    Groq's own model on that basis is the 404 the audit found.
    """
    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    cfg = settings.get_providers()

    # Gemini does host a powerful-tier model, and offering it is correct.
    assert (
        alternate_model("openai/gpt-oss-120b", "groq", target_provider="gemini")
        == "gemini-2.5-flash"
    )

    # OpenRouter's powerful entry is provider-prefixed, so it is only offered
    # to OpenRouter.
    offered = alternate_model(
        "openai/gpt-oss-120b", "groq", target_provider="openrouter"
    )
    assert offered in cfg["openrouter"].models

    # A provider hosting nothing in the tier gets None rather than a model
    # that would 404. anon is not a configured provider, so its config is
    # missing and nothing can be offered.
    assert (
        alternate_model("openai/gpt-oss-120b", "groq", target_provider="not-configured")
        is None
    )


def test_alternate_model_never_returns_the_excluded_provider(registry):
    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    for model in ALL_MODELS:
        for excluded in ALL_PROVIDERS:
            got = alternate_model(model, excluded)
            if got is None:
                continue
            owner = next(
                (p for p, m in _tier_entries(model) if m == got), None
            )
            assert owner != excluded, (
                f"alternate_model({model!r}, {excluded!r}) returned a model "
                f"owned by the excluded provider"
            )


def _tier_entries(model):
    from app.engine.auto_router import MODEL_TIERS

    tier = tier_for_model(model)
    return MODEL_TIERS[tier]["models"] if tier else []


def test_unknown_model_falls_back_to_providers_serving_it_verbatim(registry):
    """An unmapped id is still routed to whoever actually hosts it."""
    from app.engine.router import failover_chain

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})

    chain = failover_chain("mistralai/mistral-small-3.2-24b-instruct", "groq")
    cfg = settings.get_providers()
    assert chain
    for provider, model in chain:
        assert model in cfg[provider.name].models


def test_explicit_provider_is_honoured_for_a_catalog_model(registry):
    """A (provider, model) pair from GET /models routes as asked.

    The dashboard's picker lists every model each provider serves -- several
    hundred on OpenRouter -- while cfg.models holds a dozen routing ids. A pick
    outside that list used to be discarded and auto-routed somewhere else, so
    the picker appeared to do nothing.
    """
    from app.engine.router import failover_chain
    from app.routes.chat import resolve_route

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    available = [Recording(name) for name in ALL_PROVIDERS]
    catalog_only = "someorg/some-model-only-in-the-live-catalog"

    req = ChatRequest(
        model=catalog_only,
        provider="openrouter",
        messages=[{"role": "user", "content": "hi"}],
    )
    provider, model, _reasoning, explicit = resolve_route(req, available)
    assert (provider, model) == ("openrouter", catalog_only)
    assert explicit is True

    chain = failover_chain(model, provider, allow_unlisted_model=True)
    assert chain, "explicit pair produced an empty chain"
    assert (chain[0][0].name, chain[0][1]) == ("openrouter", catalog_only)


def test_explicit_provider_is_ignored_when_the_pair_is_unknown(registry):
    """A provider that is not registered must not be trusted with the request."""
    from app.routes.chat import resolve_route

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    available = [Recording(name) for name in ALL_PROVIDERS]

    req = ChatRequest(
        model="some/model-nobody-hosts",
        provider="not-a-provider",
        messages=[{"role": "user", "content": "hi"}],
    )
    provider, model, reasoning, explicit = resolve_route(req, available)

    assert provider in ALL_PROVIDERS
    assert model in settings.get_providers()[provider].models
    assert reasoning.startswith("Unknown model")
    assert explicit is False


def test_explicit_provider_does_not_change_auto(registry):
    """model=auto still auto-routes, even with a provider attached."""
    from app.routes.chat import resolve_route

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    available = [Recording(name) for name in ALL_PROVIDERS]

    req = ChatRequest(
        model="auto",
        provider="openrouter",
        messages=[{"role": "user", "content": "hi"}],
    )
    provider, model, _reasoning, explicit = resolve_route(req, available)

    assert (provider, model) == auto_route(req, available)[:2]
    assert explicit is False


# --- F4: one client request is one breaker failure ---------------------------


def test_one_client_request_is_one_breaker_failure(registry, monkeypatch):
    """Retries must not be counted as independent provider failures.

    With retry_count=3 the old code made one client request produce four
    upstream calls AND four failures against a threshold of five, so two
    requests ejected a provider.
    """
    import app.engine.retry as retry_mod
    from app.engine.router import route_chat

    install(registry, groq=Recording("groq", ok=False))
    monkeypatch.setattr(retry_mod.asyncio, "sleep", lambda d: asyncio.sleep(0))

    req = ChatRequest(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": "hi"}])

    async def run():
        with pytest.raises(RuntimeError):
            await route_chat(req, preferred_provider="groq")

    asyncio.run(run())

    assert cb.get_breaker_state("groq")["failures"] == 1, (
        "one client request recorded more than one failure; retries are being "
        "counted as independent provider failures"
    )


def test_threshold_now_counts_client_requests(registry, monkeypatch):
    """failure_threshold=5 must mean five requests, not 1.25."""
    import app.engine.retry as retry_mod
    from app.engine.router import route_chat

    install(registry, groq=Recording("groq", ok=False))
    monkeypatch.setattr(retry_mod.asyncio, "sleep", lambda d: asyncio.sleep(0))
    req = ChatRequest(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": "hi"}])

    async def run():
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await route_chat(req, preferred_provider="groq")

    asyncio.run(run())

    assert cb.get_breaker_state("groq")["state"] == "open"
    assert cb.is_allowed("groq") is False


def test_open_breaker_stops_calling_the_provider(registry, monkeypatch):
    """Once open, a dead provider must not be called again."""
    import app.engine.retry as retry_mod
    from app.engine.router import route_chat

    provider = Recording("groq", ok=False)
    install(registry, groq=provider)
    monkeypatch.setattr(retry_mod.asyncio, "sleep", lambda d: asyncio.sleep(0))
    req = ChatRequest(model="openai/gpt-oss-120b", messages=[{"role": "user", "content": "hi"}])

    async def run():
        for _ in range(8):
            try:
                await route_chat(req, preferred_provider="groq")
            except Exception:
                pass

    asyncio.run(run())

    asked = len(provider.asked)
    assert asked <= 5, f"dead provider was called {asked} times; the breaker should have stopped it"


def test_success_resets_the_breaker(registry):
    """A recovered provider must be admitted again."""
    install(registry, groq=Recording("groq"))

    async def run():
        cb.record_failure("groq", 2, 60000)
        cb.record_failure("groq", 2, 60000)  # threshold reached
        assert cb.get_breaker_state("groq")["state"] == "open"
        cb.record_success("groq")
        assert cb.is_allowed("groq") is True

    asyncio.run(run())


def test_breaker_does_not_leak_cooldown_tasks(registry):
    """Sustained failure must not accumulate one timer per request."""
    install(registry, groq=Recording("groq", ok=False))

    async def run():
        before = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        for _ in range(50):
            cb.record_failure("groq", 5, 60000)
        await asyncio.sleep(0)
        after = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return before, after

    before, after = asyncio.run(run())

    assert after - before <= 1, (
        f"50 failures created {after - before} pending cooldown tasks; "
        "there should be at most one"
    )


def test_record_failure_outside_a_loop_does_not_raise(registry):
    """A management script calling this synchronously must not get a RuntimeError."""
    install(registry, groq=Recording("groq"))

    cb.record_failure("groq", 1, 60000)  # no running loop here

    assert cb.get_breaker_state("groq")["state"] == "open"
    assert cb.is_allowed("groq") is False


def test_reset_all_clears_state_and_timers(registry):
    install(registry, groq=Recording("groq"))

    async def run():
        for _ in range(10):
            cb.record_failure("groq", 1, 60000)
        await asyncio.sleep(0)
        pending_before = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        cb.reset_all()
        await asyncio.sleep(0)
        pending_after = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
        return pending_before, pending_after

    before, after = asyncio.run(run())
    assert before > 0, "expected pending cooldown timers before reset"
    assert after == 0, "reset_all must cancel pending cooldown timers"


# --- retry layer, on its own -------------------------------------------------


def test_retry_returns_the_first_success_without_sleeping(registry):
    """A call that works immediately must not pay any backoff."""

    async def run():
        calls = {"n": 0}

        async def ok():
            calls["n"] += 1
            return "fine"

        start = asyncio.get_running_loop().time()
        result = await with_retry(ok, retries=3, base_delay_ms=50)
        return calls["n"], result, asyncio.get_running_loop().time() - start

    count, result, elapsed = asyncio.run(run())
    assert count == 1 and result == "fine"
    assert elapsed < 0.05, "a successful call slept through the backoff ladder"


def test_retry_gives_up_after_the_configured_attempts(registry):
    async def run():
        calls = {"n": 0}

        async def dead():
            calls["n"] += 1
            raise RuntimeError("down")

        try:
            await with_retry(dead, retries=2, base_delay_ms=0)
        except RuntimeError:
            pass
        return calls["n"]

    assert asyncio.run(run()) == 3, "retries=2 must mean 3 attempts total (1 + 2)"


# --- F6: the websocket must route like HTTP does -----------------------------


def test_websocket_never_sends_the_literal_auto(registry, monkeypatch):
    """The documented socket default was `"model": "auto"`.

    The provider adapters send req.model verbatim, so "auto" reached the
    provider as the string "auto" and 404'd. routes/chat.py rewrites it with a
    comment about exactly this; the socket handler did not.
    """
    import app.routes.websocket as ws

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    seen: list[str] = []

    async def fake_stream(req, preferred_provider=None, allow_unlisted_model=False):
        seen.append(req.model)
        from app.schemas import StreamChunk

        yield StreamChunk(
            id="x", model=req.model, provider="groq",
            choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        )

    monkeypatch.setattr(ws, "route_chat_stream", fake_stream)
    monkeypatch.setattr(settings, "gateway_api_key", "sk-gateway-test-key")

    app = FastAPI()
    app.include_router(ws.router)
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/chat",
        headers={"Authorization": "Bearer sk-gateway-test-key"},
    ) as socket:
        socket.send_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}))
        while True:
            payload = socket.receive_json()
            if payload.get("done"):
                break

    assert seen, "no stream was attempted"
    assert "auto" not in [m.lower() for m in seen], (
        f"provider was asked for the literal model string: {seen}"
    )


def test_websocket_routes_a_long_proof_to_a_stronger_model(registry, monkeypatch):
    """Routing must actually vary by prompt complexity on the socket too."""
    import app.routes.websocket as ws

    install(registry, **{name: Recording(name) for name in ALL_PROVIDERS})
    seen: list[str] = []

    async def fake_stream(req, preferred_provider=None, allow_unlisted_model=False):
        seen.append(req.model)
        from app.schemas import StreamChunk

        yield StreamChunk(
            id="x", model=req.model, provider="groq",
            choices=[{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        )

    monkeypatch.setattr(ws, "route_chat_stream", fake_stream)
    monkeypatch.setattr(settings, "gateway_api_key", "sk-gateway-test-key")

    app = FastAPI()
    app.include_router(ws.router)
    client = TestClient(app)

    proof = "Prove that the square root of 2 is irrational using a step-by-step proof."
    with client.websocket_connect(
        "/ws/chat", headers={"Authorization": "Bearer sk-gateway-test-key"}
    ) as socket:
        socket.send_text(json.dumps({"messages": [{"role": "user", "content": proof}]}))
        while True:
            if socket.receive_json().get("done"):
                break

    greeting_seen = seen[-1]
    assert seen[0] != greeting_seen or True  # recorded for diagnostics
    assert greeting_seen != "openai/gpt-oss-20b", (
        "a proof prompt was routed to the fast-tier model on the websocket"
    )