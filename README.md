# AI Inference Gateway

A production-style multi-provider LLM gateway with auto-routing, semantic caching, failover, cost analytics, and a Next.js dashboard. Routes every request across Groq, Gemini, and OpenRouter through a single OpenAI-style API.

Deployed live: [frontend](https://llm-gateway-ecru.vercel.app) · [API docs](https://llm-gateway-2not.onrender.com/docs)

## What it does

- **Auto-routing** — classifies each prompt (`fast` / `balanced` / `powerful`) and picks the cheapest model tier that can handle it. Backed by a golden-set eval harness (`backend/evals`) that scores routing accuracy in CI.
- **Multi-provider failover** — circuit breakers + per-request failover chain, so a dead provider never breaks a request.
- **Semantic + exact caching** — similar prompts return cached answers; cache savings are surfaced as dollars in analytics.
- **Auth + rate limiting** — per-key rate limits and `Bearer sk-gateway-*` API keys enforced by middleware.
- **Cost analytics** — per-model / per-provider usage, spend, cache savings, latency, and request history.
- **Observability** — every request carries a correlatable `X-Request-ID` and structured JSON logs.
- **Next.js dashboard** — chat UI with streaming, session history, model picker, Markdown export, and an analytics page.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · Uvicorn |
| Frontend | Next.js 16 (App Router) · React 19 · Tailwind v4 · framer-motion |
| Database | Neon PostgreSQL (SQLAlchemy async + asyncpg) |
| HTTP client | httpx (async) |
| Cache | In-memory + persisted semantic cache (embedding similarity) |
| Quality | pytest (25 tests) · routing eval harness · GitHub Actions CI · typed contract |

## Repository layout

```
backend/
  app/
    middleware/   auth, rate limit, request-ID logging, error handler
    engine/       auto-router, failover, circuit breaker, retry
    cache/        exact + semantic caching
    providers/    Groq, Gemini, OpenRouter adapters
    routes/       chat, embeddings, batch, analytics, chat history
  evals/          golden prompts + routing accuracy scorer
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

Tables auto-create on startup. The default dev gateway key is `sk-gateway-dev-key` — set a real `GATEWAY_API_KEY` in production.

## Environment variables

### Backend (`backend/.env`)

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | Neon/PostgreSQL async connection string |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Admin bypass key (`sk-gateway-*` format) |
| `GROQ_API_KEY` | — | Groq API key |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `PORT` | `8000` | API server port |
| `LOG_LEVEL` | `info` | Logging level |

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

`"model": "auto"` triggers the complexity router — try it with `"Hi"` (fast tier) vs `"Prove that the square root of 2 is irrational"` (powerful tier).

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
python -m pytest -q                       # 25 unit/integration tests
python -m evals.run --report              # routing accuracy vs golden set
python -m scripts.export_openapi          # regenerate backend/openapi.json
cd ../frontend
npm run lint && npm run build
```

CI (`.github/workflows/ci.yml`) runs the backend tests + routing eval (with a 90% accuracy floor) and frontend lint + build on every push to `main`.

## Deploy

- **Backend** — Render web service (see `render.yaml`). Uses a managed PostgreSQL database.
- **Frontend** — Vercel; pushes to `main` auto-deploy.
