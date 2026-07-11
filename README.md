# AI Inference Gateway

Multi-provider LLM gateway that routes requests across Groq, Gemini, and OpenRouter. Handles authentication, rate limiting, streaming, caching, circuit breaker failover, usage tracking, and background job processing through a single API.

## Tech Stack

| Layer | Choice |
|---|---|
| Runtime | **Python 3.11+** |
| Web framework | **FastAPI + Uvicorn** |
| Database | **Neon PostgreSQL (raw psycopg)** |
| Frontend | **Streamlit** |
| HTTP client | **httpx (async)** |
| Cache | **In-memory dict + Neon persisted** |
| Queue | **In-process asyncio** |

## Routes

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | No | Service info |
| GET | `/health` | No | Per-provider health status |
| GET | `/models` | No | All models across providers |
| GET | `/models/{provider}` | No | Models for a specific provider |
| POST | `/chat` | Yes | Chat completions (streaming via SSE) |
| POST | `/embeddings` | Yes | Text embeddings (Gemini) |
| POST | `/batch/chat` | Yes | Enqueue chat job, returns job ID |
| GET | `/batch/jobs/{id}` | Yes | Poll job result |
| GET | `/batch/queue/status` | Yes | Queue depth and state |

## Setup

```bash
# Clone and enter directory
git clone <repo>
cd ai-inference-gateway

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure .env
# DATABASE_URL, GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY are pre-set

# Run API server (port 8000)
python -m app.main

# Run Streamlit frontend (separate terminal, port 8501)
streamlit run streamlit_app.py
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | Neon PostgreSQL connection string |
| `GATEWAY_API_KEY` | `sk-gateway-dev-key` | Admin bypass key |
| `GROQ_API_KEY` | — | Groq API key |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `PORT` | `8000` | API server port |
| `LOG_LEVEL` | `info` | Logging level |

## API Examples

### Chat

```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.0-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false,
    "temperature": 0.7
  }'
```

### Embeddings

```bash
curl -X POST http://localhost:8000/embeddings \
  -H "Authorization: Bearer sk-gateway-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"input": "text to embed", "model": "text-embedding-004"}'
```

## Database Schema

Tables are auto-created on startup:

```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    key_prefix TEXT NOT NULL,
    key_hash TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    rate_limit_max INTEGER DEFAULT 60,
    rate_limit_window_ms INTEGER DEFAULT 60000,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE TABLE usage_records (
    id TEXT PRIMARY KEY,
    api_key_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_usd DOUBLE PRECISION DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT TRUE,
    cached BOOLEAN DEFAULT FALSE,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE cached_responses (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    ttl_ms INTEGER NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
```
