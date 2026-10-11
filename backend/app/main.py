import sys
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import json
import logging
import time
from contextlib import asynccontextmanager


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("method", "path", "status", "duration_ms", "api_key_id", "request_id", "error_type"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
from app.config import settings
from app.database import init_db, close_db
from app.providers.registry import initialize_providers
from app.middleware.logging import LoggingMiddleware
from app.middleware.error_handler import (
    ErrorHandlerMiddleware,
    http_exception_handler,
    validation_exception_handler,
)
from app.middleware.auth import AuthMiddleware, RateLimitMiddleware
from app.routes.chat import router as chat_router
from app.routes.batch import router as batch_router
from app.routes.api_keys import router as api_keys_router
from app.routes.analytics import router as analytics_router
from app.routes.websocket import router as ws_router
from app.routes.chat_history import router as chat_history_router
from app.routes.health import router as health_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("gateway")
for _handler in logging.getLogger().handlers:
    _handler.setFormatter(JsonFormatter())

# httpx logs the full request URL at INFO, and Gemini authenticates via an
# ?key=... query param — that writes provider API keys into logs. Keep it quiet.
for _noisy in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Inference Gateway")
    await init_db()
    initialize_providers()
    yield
    logger.info("Shutting down")
    await close_db()


app = FastAPI(
    title="AI Inference Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# Registering these replaces FastAPI's built-in handlers, which would
# otherwise emit `{"detail": ...}` while every other error used
# `{"error": {...}}`. One envelope, everywhere.
#
# Both are needed at the app level rather than only in the middleware:
# FastAPI installs its own handlers for these two exception types and runs
# them *inside* the middleware stack, so a 404 or a 422 produced by a route
# never reaches ErrorHandlerMiddleware at all. The middleware alone would
# only cover exceptions raised outside the router.
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(FastAPIHTTPException, http_exception_handler)
# Starlette's router raises its own HTTPException for unmatched routes and
# method-not-allowed, which is a different class from FastAPI's. Registering
# only the FastAPI one left a 404 answering `{"detail": "Not Found"}` while
# every other error used `{"error": {...}}`.
app.add_exception_handler(StarletteHTTPException, http_exception_handler)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(LoggingMiddleware)
_cors_origins = settings.cors_origin_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    logger.info("CORS enabled for %d configured origin(s)", len(_cors_origins))
else:
    # No CORS middleware at all when no origin is allowlisted.
    #
    # This used to be allow_origins=["*"] with allow_credentials=True, which
    # Starlette resolves into "reflect any Origin and permit credentials".
    # Auth is a bearer token here, so no browser is entitled to call this API
    # cross-origin at all, and the dashboard reaches it server-side from a
    # Next.js route handler where CORS never applies. Installing the
    # middleware with an empty allowlist instead would still emit CORS
    # headers for allowed methods on every response, which is noise at best
    # and an invitation to reintroduce the wildcard at worst.
    logger.info("CORS disabled: no origins configured, set CORS_ALLOW_ORIGINS to enable")


@app.get("/")
async def root():
    return {
        "service": "AI Inference Gateway",
        "version": "1.0.0",
        "endpoints": {
            "chat": "/chat",
            "models": "/models",
            "embeddings": "/embeddings",
            "batch": "/batch/chat",
            "queue": "/batch/queue/status",
            "health": "/health",
            "ready": "/ready",
        },
    }


app.include_router(chat_router)
app.include_router(batch_router)
app.include_router(api_keys_router)
app.include_router(analytics_router)
app.include_router(ws_router)
app.include_router(chat_history_router)
app.include_router(health_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=settings.is_dev)
