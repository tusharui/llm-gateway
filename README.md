# AI Inference Gateway

A production-style multi-provider LLM gateway with auto-routing, semantic caching, failover, cost analytics, and a Next.js dashboard. Routes every request across Groq, Gemini, and OpenRouter through a single OpenAI-style API.

Deployed live: [frontend](https://llm-gateway-ecru.vercel.app) Â· [API docs](https://llm-gateway-2not.onrender.com/docs)

## What it does

- **Auto-routing** - classifies each prompt (`fast` / `balanced` / `powerful`) and picks the cheapest model tier that can handle it. Backed by a golden-set eval harness (`backend/evals`): 391 labelled cases, confidence intervals, a confusion matrix, a learned baseline for comparison, and CI gates. It scores 0.529 [0.480-0.578] - read the honesty note under Testing & quality before quoting that.
- **Multi-provider failover** â€” circuit breakers + per-request failover chain, so a dead provider never breaks a request. Every `(provider, model)` pair in a chain is checked against that provider's configured model list, so failover never spends a round-trip on a guaranteed 404.
- **Semantic + exact caching** â€” similar prompts return cached answers; cache savings are surfaced as dollars in analytics.
- **Auth + rate limiting** â€” per-key rate limits and `Bearer sk-gateway-*` API keys enforced by middleware. Key management (`/api-keys`) and billing (`/analytics`) require the **operator** key specifically, not merely any valid key. Authentication **fails closed** when the key store is unreachable.
- **Dashboard accounts** â€” email/password sign-in backed by the same Postgres, sessions in an `HttpOnly` cookie. Passwords are PBKDF2-HMAC-SHA256 (600k iterations, stdlib); session tokens are stored as SHA-256 digests, so a database leak yields no usable credential. Separate from the API-key auth: a key authenticates a program, a session authenticates a person, and neither stands in for the other. Analytics is admin-only (`is_admin`).
- **Cost analytics** â€” per-model / per-provider usage, spend, cache savings, latency, and request history.
- **Observability** â€” every request carries a correlatable `X-Request-ID` and structured JSON logs.
- **One error envelope** â€” every failure, including validation, 404 and 405, returns `{"error": {...}}` in the OpenAI error shape.
- **Next.js dashboard** â€” chat UI with streaming, session history, model picker, Markdown export, and an analytics page.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 Â· FastAPI Â· Uvicorn |
| Frontend | Next.js 16 (App Router) Â· React 19 Â· Tailwind v4 Â· framer-motion |
| Database | Neon PostgreSQL (SQLAlchemy async + asyncpg) |
| HTTP client | httpx (async) |
| Cache | In-memory + persisted semantic cache (embedding similarity) |
| Quality | pytest (508 tests) | routing eval harness with Wilson intervals and CI gates | GitHub Actions CI | typed contract checked in CI |

## Repository layout

```
backend/
  app/
    security.py   PBKDF2 password hashing, session tokens, auth dependencies
    middleware/   auth, rate limit, request-ID logging, error handler
    engine/       auto-router, failover, circuit breaker, retry
    cache/        exact + semantic caching
    providers/    Groq, Gemini, OpenRouter adapters
    routes/       chat, embeddings, batch, analytics, chat history, auth
  evals/
    golden/       391 labelled routing cases (clear / ambiguous, adversarial, multi-turn)
    dataset.py    schema, fail-loud validation, content fingerprint
    metrics.py    Wilson intervals, subset/tier rollups, confusion matrix, significance
    baselines.py  heuristic + TF-IDF/logreg + majority floor, one interface
    learned.py    out-of-fold training, holdout, leakage inspection
    gates.py      CI quality gates, measured from the committed baseline
    report.py     deterministic results.json, atomic write
    production.py redaction + provenance for real traffic
    ERROR_ANALYSIS.md, REVIEW.md, SCHEMA.md, baseline_gates.json
  tests/          pytest suite
  scripts/        OpenAPI contract export
frontend/
  src/app/        dashboard, chat, models, analytics pages
  src/app/api/    backend proxies (request-ID aware, SSE streaming)
  src/types/      typed contract (backend.d.ts) mirroring openapi.json
```

## Run locally

```bash
# Backend (port 8000)
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env                            # fill in provider keys + DATABASE_URL
python -m uvicorn app.main:app --reload --port 8000

# Frontend (port 3000)
cd frontend
npm install
copy .env.example .env.local                      # NEXT_PUBLIC_BACKEND_URL, GATEWAY_API_KEY
npm run dev
```

Tables are **not** created by the app. Alembic owns the schema: run `python -m alembic upgrade head` before starting, and the app refuses to serve database-backed routes until the expected tables are present. `render.yaml` does this as a `preDeployCommand`. The default dev gateway key is `sk-gateway-dev-key` â€” set a real `GATEWAY_API_KEY` in production.

## Environment variables

### Backend (`backend/.env`)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | â€” | Neon/PostgreSQL async connection string |
| `DATABASE_SSL` | `require` | TLS to Postgres. `require` or `disable`; no `auto` (probing would send the password in cleartext) |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Admin bypass key (`sk-gateway-*` format). Also the only key that can reach `/api-keys` and `/analytics` |
| `GROQ_API_KEY` | â€” | Groq API key |
| `GEMINI_API_KEY` | â€” | Google Gemini API key |
| `OPENROUTER_API_KEY` | â€” | OpenRouter API key |
| `PORT` | `8000` | API server port |
| `LOG_LEVEL` | `info` | Logging level |
| `CORS_ALLOW_ORIGINS` | *(empty)* | Comma-separated browser origins. Empty = CORS disabled, which is correct when the dashboard calls the API server-side |
| `WS_MAX_MESSAGE_BYTES` | `65536` | Max inbound websocket frame; keep in sync with uvicorn `--ws-max-size` |
| `WS_MAX_CONNECTIONS_PER_KEY` | `8` | Max simultaneous websocket connections per key |
| `SESSION_COOKIE_NAME` | `gw_session` | Name of the dashboard session cookie |
| `SESSION_TTL_SECONDS` | `604800` | Session lifetime. Set on both the cookie `Max-Age` and `auth_sessions.expires_at` |
| `ALLOW_REGISTRATION` | `true` | Set `false` in production so accounts are created with `scripts/create_user` |
| `PASSWORD_HASH_ITERATIONS` | `600000` | PBKDF2 cost. Stored inside each hash, so raising it rehashes on next login |
| `AUTH_RATE_LIMIT_MAX` | `10` | Login/register attempts per IP per window |
| `AUTH_RATE_LIMIT_WINDOW_MS` | `60000` | Window for the above |
| `TRUST_FORWARDED_FOR` | `false` | Use `X-Forwarded-For` for the per-IP bucket. Enable only behind a proxy that overwrites the header |

### Frontend (`frontend/.env.local`)

| Variable | Default | Description |
|---|---|---|
| `BACKEND_URL` | `http://localhost:8000` | Backend base URL (server-side proxy target) |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Key injected by the proxy for authenticated routes |

## API examples

### Chat (streaming via SSE)

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain quicksort"}],
    "stream": true
  }'
```

`"model": "auto"` triggers the complexity router â€” try it with `"Hi"` (fast tier) vs `"Prove that the square root of 2 is irrational"` (powerful tier).

### Inspect routing decision

```bash
curl -X POST http://localhost:8000/routing/classify \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Write a Python quicksort"}]}'
```

### Analytics

```bash
curl -H "Authorization: Bearer sk-gateway-dev-key" "http://localhost:8000/analytics/summary"
curl -H "Authorization: Bearer sk-gateway-dev-key" "http://localhost:8000/analytics/by-model"
curl -H "Authorization: Bearer sk-gateway-dev-key" "http://localhost:8000/analytics/recent?limit=20"
```

Full interactive docs at `http://localhost:8000/docs`.

## Testing & quality

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q                       # 508 unit/integration tests
python -m evals.run                       # routing eval: both baselines + gates
python -m evals.run --report              # + a per-case table
python -m evals.run --load-baseline evals/baseline_gates.json   # CI behaviour, exit 1 on failure
python -m scripts.export_openapi          # regenerate backend/openapi.json
cd ../frontend
npm run lint && npm run build
```

CI (`.github/workflows/ci.yml`) runs the backend tests + the routing eval +
frontend lint + build on every push to `main`.

### What the routing eval measures

`python -m evals.run` scores the `fast` / `balanced` / `powerful` classifier
against 391 hand-labelled cases and writes `backend/evals/results.json`. It
reports, for every system, accuracy with a Wilson 95% interval overall and per
difficulty, per tier, per category and per adversarial tag, plus a
machine-readable confusion matrix. Two systems are compared: the production
heuristic and a TF-IDF + logistic regression baseline scored from stratified
group k-fold out-of-fold predictions, alongside a majority-class floor so the
headline has a reference point.

Current measurements, with intervals:

| system | overall | clear | ambiguous | powerful tier |
|---|---|---|---|---|
| heuristic | 0.529 [0.480-0.578] | 0.552 [0.494-0.610] | 0.474 [0.384-0.565] | 0.250 |
| TF-IDF + logreg | 0.703 [0.656-0.746] | 0.747 [0.693-0.795] | 0.596 [0.505-0.682] | 0.467 |
| majority floor | 0.379 [0.333-0.426] | 0.350 | 0.456 | 0.000 |

**The heuristic is not good, and the previous CI floor was measuring the test
set rather than the router.** The old gate was `--min-accuracy 0.9` against an
18-case golden set that scored 18/18; six of those cases were near-copies of
the router's own regexes and none exercised multi-turn input. The committed
floors in `backend/evals/baseline_gates.json` are 0.49 / 0.52 / 0.40 / 0.40,
each derived as `floor(observed âˆ’ 1.3 Ã— standard error)`, and each sitting next
to the measurement it came from. Raising them back to 0.90 is possible two
ways â€” make the router better, or make the dataset worse â€” and only the first
is worth doing.

Three findings worth knowing before you touch the router:

- **It fails open.** 138 of 184 errors are under-routes: genuinely hard prompts
  sent to a cheaper model. It predicts `powerful` for 34 of 391 prompts when 92
  need it. Under-routing costs answer quality silently; over-routing costs
  money, which the cost tracker can already see.
- **Clear and ambiguous are not significantly different** (0.560 vs 0.469,
  p = 0.157). The assumption that a transparent rule works on obvious cases and
  breaks on ambiguous ones is not supported by this data.
- **Two of the five failure modes are bugs, not limits.**
  `\bplan\w*\b` matches "planet" and "plant", so every sentence containing
  *planet* routes to a bigger model; and `summarise`, `how do I` and
  `condense` are missing from the keyword list entirely, so all eight how-to
  cases score zero.

`backend/evals/ERROR_ANALYSIS.md` has the full analysis with case ids and
recomputed scores, `backend/evals/REVIEW.md` has 23 manually reviewed
misclassifications, and `backend/evals/SCHEMA.md` documents the dataset format
and how to add a case.

## Deploy

- **Backend** â€” Render web service (see `render.yaml`). Uses a managed PostgreSQL database.
- **Frontend** â€” Vercel; pushes to `main` auto-deploy.
