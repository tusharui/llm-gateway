# LLM Gateway

A multi-provider LLM gateway: one OpenAI-shaped API in front of Groq, Gemini and OpenRouter, with prompt-complexity routing, semantic caching, per-provider failover, circuit breakers, cost analytics and a Next.js dashboard.

Live: **dashboard** [llm-gateway-ecru.vercel.app](https://llm-gateway-ecru.vercel.app) | **API docs** [llm-gateway-2not.onrender.com/docs](https://llm-gateway-2not.onrender.com/docs)  [50 seconds delay due deployed on free server ]

---

## What it does

| | |
|---|---|
| **Auto-routing** | `"model": "auto"` scores the prompt and picks the cheapest tier that can handle it: `fast`, `balanced` or `powerful`. Measured against 391 hand-labelled cases, not vibes - see [Testing and quality](#testing-and-quality). |
| **Multi-provider failover** | Every request carries a failover chain. A dead, slow or rate-limited provider is skipped instead of failing the call. Circuit breakers stop a dead provider being retried. |
| **Semantic + exact caching** | Repeat prompts return instantly. Near-identical prompts are matched by embedding similarity and the saving is reported in dollars. |
| **Two independent auth systems** | API keys for programs, email/password sessions for people in the browser. Neither can stand in for the other. |
| **Rate limiting** | Per-key request budget, per-IP budget on login/register, per-connection caps on websockets. |
| **Cost analytics** | Per-model and per-provider spend, token counts, latency, cache savings and a request history. |
| **Observability** | Every response carries a correlatable `X-Request-ID`; logs are structured JSON. |
| **One error envelope** | Every failure - validation, 404, 405, provider outage - returns `{"error": {...}}` in the OpenAI error shape. |
| **Dashboard** | Streaming chat with session history and Markdown export, a live model catalogue, and a spend dashboard. |

---

## Architecture

The system is one request path with a browser at each end. The dashboard is not a separate application that talks to the API; it is a browser UI plus a thin backend-for-frontend, and all provider traffic is server-side.

```
  Browser
    |  cookie: gw_session
    v
+--------------------------------------------------------------+
|  Next.js 16  (Vercel)                                        |
|                                                              |
|  (dashboard)/*   chat, models, analytics - client components  |
|  src/proxy.ts    cookie presence check -> /login             |
|  app/api/*       backend-for-frontend (BFF)                  |
|                    - verifies the session server-side        |
|                    - injects the gateway API key             |
|                    - streams SSE back unbuffered             |
+--------------------------------------------------------------+
    |  server-side fetch only; the browser never calls the API
    v
+--------------------------------------------------------------+
|  FastAPI gateway  (Render)                                   |
|                                                              |
|  Middleware:  request-id -> error envelope -> auth -> limit |
|                                                              |
|  /chat                                                        |
|    |- semantic cache (embedding similarity >= 0.92)          |
|    |- exact cache   (hash of model + messages)               |
|    |- router        prompt -> fast | balanced | powerful     |
|    |- retry         transient errors only, jittered backoff  |
|    |- failover      ordered chain of (provider, model) pairs |
|    |- circuit break skip providers that are already failing  |
|    `- usage         tokens + cost + latency -> Postgres      |
|                                                              |
|  /batch     in-process job queue (5 concurrent)              |
|  /ws/chat   streaming transport, 5 bounds on the socket       |
|  /auth      dashboard sessions (PBKDF2 + SHA-256 digests)    |
|  /api-keys  key minting (operator only)                      |
|  /analytics spend reporting (operator only)                  |
+--------------------------------------------------------------+
    |                          |                    |
    v                          v                    v
+---------+              +-----------+        +-------------+
|  Groq   |              |  Gemini   |        | OpenRouter  |
+---------+              +-----------+        +-------------+

+--------------------------------------------------------------+
|  Neon PostgreSQL          written by the gateway, not by the |
|    providers:  usage_records, cached_responses,             |
|    semantic_cache, chat_sessions/messages, api_keys,        |
|    users, auth_sessions                                      |
+--------------------------------------------------------------+
```

### The path of a single request

1. **Browser -> Next.js.** The page is a client component that fetches its own `/api/*` routes. `src/proxy.ts` only checks that the `gw_session` cookie is present - a presence check, not a verification - so a forged cookie passes it and is caught one layer later.
2. **Next.js -> FastAPI.** The BFF handler in `app/api/_auth.ts` resolves the real user with `getSession()` (a server-side `GET /auth/me`), requires an admin session for analytics routes, then attaches `Authorization: Bearer $GATEWAY_API_KEY`. It relays the upstream status and body verbatim so the gateway's error envelope survives the hop, and returns `502 Backend unreachable` only on a transport failure or an upstream 502/503/504.
3. **Middleware stack.** Four middlewares run before the route handler, outermost first: `LoggingMiddleware` mints or adopts an `X-Request-ID` and stamps `X-Response-Time-MS` on the way out; `ErrorHandlerMiddleware` converts anything unhandled into the `{"error": {...}}` envelope; `AuthMiddleware` resolves the bearer token and enforces the operator check; `RateLimitMiddleware` charges the key's budget and emits `X-RateLimit-*`. A `CORSMiddleware` wraps all of them, but only when `CORS_ALLOW_ORIGINS` is set - otherwise it is not installed at all.
4. **Route resolution.** `resolve_route()` decides which model answers before any network call. `auto` scores the prompt; an explicit `model` is matched against each provider's configured list; `provider` + `model` together pin the pair even when the model is not in the configured list.
5. **Cache.** Semantic lookup first (embeddings via Gemini, cosine similarity against live rows, threshold `0.92`), then exact lookup. A hit returns immediately with `cache_hit: true` and the similarity that matched.
6. **Provider call.** `route_chat()` wraps the adapter in `with_retry()`, records success or failure on the circuit breaker, and returns. On failure the handler falls back to `route_with_failover()`, which walks the chain.
7. **Accounting.** `track_usage()` writes provider, model, tokens, cost and latency to `usage_records`, then commits. A failure to write is swallowed - analytics must never break a completion.
8. **Stream back.** For `stream: true` the gateway emits SSE (`event: chunk` / `done` / `error`); the BFF passes the body through with `X-Accel-Buffering: no` so no proxy buffers it.

### Where things live

| Concern | Backend | Frontend |
|---|---|---|
| Routing, failover, breakers | `app/engine/` | - |
| Provider adapters | `app/providers/` | - |
| Caching | `app/cache/` | - |
| API-key auth, rate limits | `app/middleware/auth.py` | - |
| Dashboard sessions, passwords | `app/security.py`, `app/routes/auth.py` | `lib/session.ts`, `app/actions/auth.ts` |
| Persistence | `app/models.py` + Alembic | - |
| UI | - | `app/(dashboard)/`, `components/` |
| Backend-for-frontend | - | `app/api/` |
| Typed contract | `openapi.json` (generated, CI-checked) | `src/types/backend.d.ts` (hand-maintained) |

---

## How routing works

`app/engine/auto_router.py` scores the **last user message** with weighted regex signals, then thresholds the total:

| Signal | Weight |
|---|---|
| Length (chars > 500 / > 200 / > 50) | +3 / +2 / +1 |
| Length (words > 100 / > 40 / > 15) | +3 / +2 / +1 |
| Asking keywords (`explain`, `compare`, `review`, `summarise`, `how do I`...) | +2 each |
| Production keywords (`prove`, `derive`, `implement`, `architect`, `analyse`...) | +4 each |
| Construction patterns (`write`/`build`/`draft` + an artifact noun) | +4 each |
| Explicit output length (`500 words`, `3 pages`) | +4 each |
| Simple patterns (greetings, bare facts) | **-3** each |
| Conversation length (> 10 / > 20 messages) | +1 / +1 |
| System message present | +1 |

`score >= 5` -> `powerful`, `>= 2` -> `balanced`, otherwise `fast`.

Asking and producing work are weighted differently on purpose. Asking *about* something and *producing* a hard artifact are not the same amount of effort, and scoring both equally meant a prompt naming one production verb could never reach `powerful` on its own.

Each tier is an ordered list of candidates; the first one whose provider is available wins.

| Tier | Candidates (first available wins) | Stated cost / 1k |
|---|---|---|
| `fast` | groq `openai/gpt-oss-20b` -> gemini `gemini-2.5-flash-lite` -> openrouter `openai/gpt-4o-mini` | $0.0001 |
| `balanced` | groq `qwen/qwen3.8-27b` -> openrouter `openai/gpt-4o-mini` -> gemini `gemini-2.5-flash` -> openrouter `mistralai/mistral-small-3.2-24b-instruct` | $0.0005 |
| `powerful` | groq `openai/gpt-oss-120b` -> gemini `gemini-2.5-flash` -> openrouter `meta-llama/llama-3.3-70b-instruct` | $0.002 |

### Choosing a model explicitly

| Request | Result |
|---|---|
| `model: "auto"` | Scored by the router. |
| `model: "llama-3.3-70b-instruct"` | Routed to whichever configured provider lists it. |
| `model: "...", provider: "openrouter"` | Pinned to that provider. |
| `model: "<new model>", provider: "openrouter"` | Honoured even if not yet in the configured list, so a new model works the day the provider ships it. |
| `model: "groq/llama-3.3-70b"` | **Not** parsed as a prefix. It is an unknown id, so the router runs and the decision is reported as `Unknown model ...`. Send `provider` as its own field. |

Ask the router what it would do before spending anything:

```bash
curl -X POST http://localhost:8000/routing/classify \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Prove that the square root of 2 is irrational"}]}'
```

```json
{
  "tier": "powerful",
  "selected_provider": "groq",
  "selected_model": "openai/gpt-oss-120b",
  "reasoning": "Complexity=powerful | msgs=1 | chars=48"
}
```

---

## Resilience

Three independent mechanisms, each covering a different failure:

- **Retry** (`engine/retry.py`) - only transient failures: `408`, `409`, `425`, `429` and `5xx`. A `4xx` like `400` or `404` fails over immediately instead of sleeping through the whole backoff ladder. Backoff is `min(base * 2^attempt, 30s)` with 50-100% jitter. Three retries means four attempts total.
- **Failover** (`engine/failover.py`, `engine/router.py`) - an ordered chain of `(provider, model)` pairs: the requested provider first, then substitutes from each remaining provider. Every pair is checked against that provider's configured model list before it enters the chain, so failover never spends a round trip on a guaranteed 404. A stream that has already emitted a chunk re-raises rather than splicing a second provider's answer into the middle of one response.
- **Circuit breaker** (`engine/circuit_breaker.py`) - `closed` -> `open` after `failure_threshold` (5) consecutive failures -> `half-open` after `cooldown_ms` (60s). Only `open` rejects traffic, so `half-open` lets probes through to test recovery. One timer is armed per open breaker, not one per failure.

---

## Caching

Two layers, checked in this order on every non-streaming request:

| Layer | Key | Hit condition |
|---|---|---|
| **Semantic** | Embedding of the whole conversation | cosine similarity >= `0.92` against any live row for the same model |
| **Exact** | `md5(model + messages)` | key present and not expired |

Both write to Postgres and both expire on a TTL (`CACHE_TTL_MS`, default 5 minutes) checked at read time. A hit returns the stored response with `cache_hit: true` and `cache_similarity`, and is recorded in analytics as a saving rather than a charge.

Streaming responses are never cached, and the semantic cache embeds through Gemini - without a Gemini key that layer silently returns no matches.

---

## Security

**API keys** authenticate programs. Minted keys look like `sk-gw-<43 chars>`; the operator key set via `GATEWAY_API_KEY` must start with `sk-gateway-` and is compared in constant time without touching the database, so it keeps working during an outage. Either way the raw key is returned exactly once, at creation, and only its SHA-256 digest plus a 12-character display prefix is stored. Every authenticated route needs `Authorization: Bearer <key>`. `/api-keys` and `/analytics` need the **operator** key specifically - a valid tenant key gets `403`, not `404`. If the key store is unreachable, auth **fails closed** with `503`.

**Sessions** authenticate people. Passwords are PBKDF2-HMAC-SHA256 at 600k iterations (stdlib - no native crypto dependency in the image), encoded as `pbkdf2_sha256$<iters>$<salt>$<digest>`. Raising `PASSWORD_HASH_ITERATIONS` rehashes on next login instead of invalidating anyone. Session tokens are stored as SHA-256 digests in an `HttpOnly` cookie, so a database leak yields no usable credential. A non-existent account is verified against a dummy hash so it costs the same as a wrong password.

**Redaction.** `app/redact.py` scrubs `?key=`, `Bearer ...`, `sk-...`, `AIza...`, `AQ...` and `user:password@host` out of every error message and log line, because Gemini authenticates by query parameter and httpx logs full URLs at INFO.

---

## Quick start

**Requirements:** Python 3.12, Node 20.9+ (CI uses 22), and a PostgreSQL database - [Neon](https://neon.tech) works well and its free tier is enough.

```bash
# --- 1. Backend on :8000 -------------------------------------------------
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env          # macOS / Linux: cp .env.example .env
# Fill in DATABASE_URL and at least one of GROQ_API_KEY / GEMINI_API_KEY /
# OPENROUTER_API_KEY. Without a provider key every route still works but
# nothing can answer.
python -m alembic upgrade head   # REQUIRED - see below
python -m uvicorn app.main:app --reload --port 8000
```

The app **never creates tables**. Alembic owns the schema; on startup the gateway asserts every expected table exists and refuses database-backed routes until it does, so run `alembic upgrade head` first or everything returns 503. `GET /ready` will tell you exactly what is missing.

```bash
# --- 2. Frontend on :3000 ----------------------------------------------
cd frontend
npm install
copy .env.example .env.local    # macOS / Linux: cp .env.example .env.local
npm run dev
```

`BACKEND_URL` defaults to `http://localhost:8000`. Create a dashboard account at <http://localhost:3000/register> and sign in.

API docs: <http://localhost:8000/docs>

---

## Configuration

### Backend - `backend/.env`

Copy `backend/.env.example`. Only `DATABASE_URL` and one provider key are required.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | - | PostgreSQL async connection string. |
| `DATABASE_SSL` | `require` | `require` or `disable`. No `auto`: choosing TLS by probing sends the password in cleartext to whatever host the URL names. |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Operator key. **Set a real one in production.** |
| `GROQ_API_KEY` | - | Groq. Enables the `groq` provider. |
| `GEMINI_API_KEY` | - | Gemini. Also powers `/embeddings` and the semantic cache. |
| `OPENROUTER_API_KEY` | - | OpenRouter. |
| `PORT` | `8000` | API server port. |
| `NODE_ENV` | `development` | `development` enables `--reload` and non-secure cookies. Set `production` when deployed. |
| `LOG_LEVEL` | `info` | Log level. Logs are JSON either way. |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Requests per key per window. |
| `RATE_LIMIT_WINDOW_MS` | `60000` | Window for the above. |
| `CACHE_TTL_MS` | `300000` | Cache TTL for both layers. |
| `CORS_ALLOW_ORIGINS` | *(empty)* | Comma-separated origins. Empty disables CORS, which is correct: the dashboard calls the API server-side, where CORS does not apply. |
| `ALLOW_REGISTRATION` | `true` | Set `false` in production and mint accounts with `python -m scripts.create_user --email you@example.com --admin`. |
| `SESSION_COOKIE_NAME` | `gw_session` | Dashboard session cookie name. |
| `SESSION_TTL_SECONDS` | `604800` | Session lifetime, applied to the cookie and the DB row together. |
| `PASSWORD_HASH_ITERATIONS` | `600000` | PBKDF2 cost, stored inside each hash so raising it rehashes on next login. |
| `AUTH_RATE_LIMIT_MAX` | `10` | Login/register attempts per IP per window. |
| `AUTH_RATE_LIMIT_WINDOW_MS` | `60000` | Window for the above. |
| `TRUST_FORWARDED_FOR` | `false` | Use `X-Forwarded-For` for the per-IP bucket. Enable only behind a proxy that overwrites the header, or the limiter is free to bypass. |
| `WS_MAX_MESSAGE_BYTES` | `65536` | Max inbound websocket frame. Keep in sync with uvicorn `--ws-max-size`. |
| `WS_MAX_MESSAGES_PER_REQUEST` | `64` | Max entries in one frame's `messages` array. |
| `WS_MAX_TURNS_PER_CONNECTION` | `200` | Request cycles per socket before it is closed. |
| `WS_RATE_LIMIT` | `true` | Share the HTTP rate-limit budget with websockets. |
| `WS_MAX_CONNECTIONS_PER_KEY` | `8` | Simultaneous sockets per key. |
| `ALLOW_DESTRUCTIVE_MIGRATIONS` | unset | Must be `1` to permit `alembic downgrade` against a non-local database. |

### Frontend - `frontend/.env.local`

| Variable | Default | Description |
|---|---|---|
| `BACKEND_URL` | `http://localhost:8000` | Backend base URL, used server-side by the BFF. Must be reachable from the frontend host. |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Key the BFF injects for authenticated routes. Set a real one in production. |
| `SESSION_COOKIE_NAME` | `gw_session` | Must match the backend. |

---

## Using the API

Every authenticated request needs `Authorization: Bearer <key>`. Errors are always `{"error": {"message", "type", "code"}}`.

### Chat

```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain quicksort"}]
  }'
```

```json
{
  "id": "chatcmpl-...",
  "model": "openai/gpt-oss-20b",
  "provider": "groq",
  "latency_ms": 412,
  "routing_decision": "Complexity=fast | msgs=1 | chars=16",
  "cache_hit": false,
  "cache_similarity": null,
  "choices": [{ "index": 0, "message": { "role": "assistant", "content": "..." }, "finish_reason": "stop" }],
  "usage": { "prompt_tokens": 12, "completion_tokens": 240, "total_tokens": 252 }
}
```

`provider`, `latency_ms`, `routing_decision`, `cache_hit` and `cache_similarity` are gateway additions on top of the OpenAI shape.

### Streaming

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Write a haiku about caching"}],"stream":true}'
```

```
event: chunk
data: {"id":"...","model":"openai/gpt-oss-20b","provider":"groq","choices":[{"index":0,"delta":{"content":"Cach"}}]}

event: chunk
data: {"id":"...","model":"openai/gpt-oss-20b","provider":"groq","choices":[{"index":0,"delta":{"content":"ing"}}]}

event: done
data: {"token_count":18,"routing_decision":"Complexity=fast | ...","provider":"groq","model":"openai/gpt-oss-20b"}
```

Events are `chunk` per delta, then exactly one `done` or `error`.

### Pin a provider and model

```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/llama-3.3-70b-instruct",
    "provider": "openrouter",
    "temperature": 0.3,
    "max_tokens": 800,
    "messages": [{"role":"user","content":"Summarise the CAP theorem"}]
  }'
```

### Other parameters

`stream`, `temperature` (0-2), `max_tokens`, `top_p` (0-1) are all supported on `/chat` and over the websocket.

### From the OpenAI SDK

The gateway is shaped like OpenAI, so this works with a swapped base URL:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000",   # or your deployed host
    api_key="sk-gateway-dev-key",
)

stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Prove that the square root of 2 is irrational"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### Batch, history and websockets

```bash
# Queue a job
curl -X POST http://localhost:8000/batch/chat -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"summarise this"}]}'
# -> {"job_id": "...", "status": "pending"}
curl http://localhost:8000/batch/jobs/<job_id> -H "Authorization: Bearer $KEY"
curl http://localhost:8000/batch/queue/status   -H "Authorization: Bearer $KEY"
```

Session transcripts persist in Postgres and back the dashboard's history:

```bash
curl http://localhost:8000/chat-history/sessions -H "Authorization: Bearer $KEY"
```

For long-lived connections, `WS /ws/chat` authenticates via the `Authorization` header or a `bearer` subprotocol - **never** a query parameter, which is rejected with close code `1008` because URLs get logged. Close codes: `1008` policy (bad token, caps hit), `1009` frame too large, `1011` internal error, `1013` rate limited.

### Keys and analytics (operator key only)

```bash
# Mint a key - the raw value is returned once and never again
curl -X POST http://localhost:8000/api-keys -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" -d '{"name":"ci","rate_limit_max":120}'

# Spend
curl "http://localhost:8000/analytics/summary?days=7"   -H "Authorization: Bearer sk-gateway-dev-key"
curl "http://localhost:8000/analytics/by-model?days=30"  -H "Authorization: Bearer sk-gateway-dev-key"
curl "http://localhost:8000/analytics/by-provider"       -H "Authorization: Bearer sk-gateway-dev-key"
curl "http://localhost:8000/analytics/timeline?days=7"   -H "Authorization: Bearer sk-gateway-dev-key"
curl "http://localhost:8000/analytics/recent?limit=20"   -H "Authorization: Bearer sk-gateway-dev-key"
```

---

## Using the dashboard

`npm run dev`, then <http://localhost:3000>. Register, then:

- **Dashboard** - request volume, tokens, providers, average latency, total cost, provider health, backend reachability. Polls every 15s.
- **Chat** - streaming responses, model picker with live catalogue search, persisted sessions, export to Markdown. Pick **auto** to hand routing to the gateway, or pin a specific provider and model. The cache badge shows the similarity that matched.
- **Models** - every model the configured providers currently advertise, with context length and pricing per 1M tokens. Fetched live, so it reflects what the providers actually serve.
- **Analytics** - admin accounts only. Spend, cache savings and hit rate over 1/7/30/90 days, top models, provider breakdown and recent requests.

---

## API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/` | - | Service name, version, endpoint map |
| `GET` | `/health` | - | Liveness. Does not touch the database |
| `GET` | `/ready` | - | Readiness: schema present, DB reachable, provider health |
| `POST` | `/chat` | key | Completion, streaming or not |
| `GET` | `/models` | - | Live model catalogue across all providers |
| `GET` | `/models/{provider}` | - | Config-listed models for one provider |
| `GET` | `/routing` | - | Tier definitions and classification rules |
| `POST` | `/routing/classify` | - | Dry-run the router without spending |
| `POST` | `/embeddings` | key | Gemini `embedContent` |
| `POST` | `/batch/chat` | key | Enqueue a chat job |
| `GET` | `/batch/jobs/{id}` | key | Job status and result |
| `GET` | `/batch/queue/status` | key | Queue depth |
| `GET`/`POST` | `/chat-history/sessions` | key | List / create sessions |
| `GET`/`PATCH`/`DELETE` | `/chat-history/sessions/{id}` | key | Read, rename, delete |
| `POST` | `/chat-history/sessions/{id}/messages` | key | Append a message |
| `POST`/`GET` | `/api-keys` | **operator** | Mint / list keys |
| `GET`/`PATCH`/`DELETE` | `/api-keys/{id}` | **operator** | Inspect, update, revoke |
| `POST` | `/api-keys/{id}/toggle` | **operator** | Enable / disable |
| `GET` | `/analytics/{summary,by-provider,by-model,timeline,recent}` | **operator** | Spend reporting |
| `POST` | `/auth/register` | - | Create a dashboard account |
| `POST` | `/auth/login` | - | Sign in, set session cookie |
| `POST` | `/auth/logout` | session | End the session |
| `GET` | `/auth/me` | session | Current user |
| `POST` | `/auth/change-password` | session | Rotate password, revoke other sessions |
| `WS` | `/ws/chat` | key | Streaming transport |

Interactive docs at `/docs`.

---

## Testing and quality

```bash
cd backend
pip install -r requirements-dev.txt

python -m pytest -q                                   # 568 tests
python -m evals.run                                   # routing eval
python -m evals.run --report                          # + a per-case table
python -m evals.run --load-baseline evals/baseline_gates.json   # CI mode, exit 1 on failure
python -m evals.run --dump-failures                   # every misclassification
python -m scripts.export_openapi                      # regenerate openapi.json

cd ../frontend
npm run lint && npm run build
```

CI (`.github/workflows/ci.yml`) runs on every push to `main`: pytest against a real PostgreSQL 18 service, `alembic check` for model/migration drift, a byte-diff that fails if `openapi.json` is stale, the routing eval against its committed gates, then frontend lint and build on Node 22.

### Routing accuracy

The router is scored against 391 labelled cases in `backend/evals/golden` (clear / ambiguous / adversarial / multi-turn / long-conversation). Accuracy comes with a Wilson 95% interval, per-difficulty and per-tier rollups, and a confusion matrix. Two reference points are reported alongside it: a TF-IDF + logistic regression baseline scored out-of-fold, and a majority-class floor.

| system | overall | clear | ambiguous | adversarial | `fast` | `balanced` | `powerful` |
|---|---|---|---|---|---|---|---|
| **heuristic (shipped)** | **0.678** [0.630-0.722] | 0.744 | 0.518 | 0.515 | **0.772** | 0.553 | **0.728** |
| TF-IDF + logreg | 0.706 [0.659-0.749] | 0.765 | 0.561 | 0.691 | 0.758 | 0.767 | 0.522 |
| majority floor | 0.381 [0.334-0.430] | 0.339 | 0.482 | 0.381 | 0.195 | 0.800 | 0.000 |

Read this honestly:

- **The router is not state of the art and does not need to be.** The learned baseline is 2.8 points ahead overall, but on the 365 single-message cases where both systems saw byte-identical input the gap is **1.1 points at McNemar p = 0.80** - a coin flip. They fail in opposite directions: the router is better at both ends, the learned model at the middle. Shipping the learned model would mean fitting on the only evaluation set this project has (out-of-fold 0.706 versus 0.985 in-sample - that 28-point gap is memorisation). Full reasoning, including the three fidelity bugs found while proving the artifact could be exported, is in [`backend/evals/ROUTING_MODEL_DECISION.md`](backend/evals/ROUTING_MODEL_DECISION.md).
- **`balanced` is the weak tier** at 0.553. The confusion matrix shows 53 of 150 balanced prompts being under-routed to `fast` - the largest single error cluster. Over-routing (48 cases) costs money, which analytics already catches; under-routing costs answer quality silently.
- **The gate is 0.49, and that is deliberate.** The previous floor was 0.90 against an 18-case set the router scored 18/18 on - a number that measured the test set, not the router, since six of those cases were near-copies of the router's own regexes. Every committed threshold in `backend/evals/baseline_gates.json` is derived as `floor(observed - 1.3 * standard error)` and sits next to the measurement it came from. Getting the floor back to 0.90 is possible two ways: make the router better, or make the dataset worse. Only the first is worth doing.
- **The router has been tuned against this dataset.** The same person wrote the regexes and the labels, so 0.678 is optimistic by an unknown amount. `backend/evals/production/` is the escape hatch - it scores real redacted traffic - and it has never been run because there is no traffic export yet.

Supporting analysis lives beside the harness: [`ERROR_ANALYSIS.md`](backend/evals/ERROR_ANALYSIS.md) (per-case failure mechanisms; its headline numbers predate the current router and it says so at the top), [`REVIEW.md`](backend/evals/REVIEW.md) (23 misclassifications read by hand), [`SCHEMA.md`](backend/evals/SCHEMA.md) (dataset format and how to add a case).

---

## Deploy

- **Backend** - Render web service, defined in `render.yaml`. `preDeployCommand` runs `alembic upgrade head`; `healthCheckPath` is `/ready`, not `/health`, so a broken instance stops receiving traffic instead of being declared healthy while every database route 503s.
- **Frontend** - Vercel, auto-deploying from `main`.
- **Database** - Neon PostgreSQL.

Set `GATEWAY_API_KEY`, `ALLOW_REGISTRATION=false`, `NODE_ENV=production` and `DATABASE_SSL=require` in both environments. The default dev key is public knowledge.

---

## Repository layout

```
backend/
  app/
    main.py           app wiring, middleware stack, lifespan
    config.py         settings + per-provider config
    database.py       async engine, schema assertion, db_status
    models.py         SQLAlchemy tables (mirrors migrations exactly)
    schemas.py        pydantic request/response models
    security.py       PBKDF2 passwords, session tokens, auth dependencies
    redact.py         credential scrubbing for logs and errors
    migration_safety.py  blocks alembic downgrade against a real database
    engine/
      auto_router.py  prompt scoring, tiers, model selection
      router.py       provider pick, failover chain, chat + stream
      failover.py     non-streaming failover walk
      circuit_breaker.py  closed/open/half-open per provider
      retry.py        transient-only retry with jittered backoff
    cache/
      cache.py        exact cache (in-memory + Postgres)
      semantic_cache.py  embedding similarity cache
    providers/
      interface.py    AIProvider ABC
      registry.py     availability from config + key presence
      groq.py  gemini.py  openrouter.py
    middleware/
      auth.py         bearer auth, operator check, rate limiting
      logging.py      request id + timing
      error_handler.py  the {"error": {...}} envelope
    routes/
      chat.py  batch.py  websocket.py  chat_history.py
      analytics.py  api_keys.py  auth.py  health.py
    queue/job_queue.py  in-process batch executor
  evals/               routing eval harness (see above)
  migrations/versions/ 0001 baseline, 0002 indexes, 0003 user accounts
  scripts/             create_user, export_openapi, diff_schema, seed_prod_sim
  tests/               568 pytest tests
frontend/
  src/
    app/
      (dashboard)/    dashboard, chat, models, analytics
      login/  register/
      api/            backend-for-frontend route handlers
      actions/auth.ts server actions for sign in/out
    components/       Sidebar, PageTransition
    lib/session.ts    server-side session helper
    proxy.ts          cookie presence check (Next 16 renamed middleware)
    types/backend.d.ts  hand-maintained typed contract
render.yaml            Render blueprint
```
