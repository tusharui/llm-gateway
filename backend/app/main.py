import sys
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.routes.chat import router as chat_router
from app.routes.batch import router as batch_router
from app.routes.api_keys import router as api_keys_router
from app.routes.analytics import router as analytics_router
from app.routes.websocket import router as ws_router
from app.routes.chat_history import router as chat_history_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("gateway")
for _handler in logging.getLogger().handlers:
    _handler.setFormatter(JsonFormatter())


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

app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        },
    }


app.include_router(chat_router)
app.include_router(batch_router)
app.include_router(api_keys_router)
app.include_router(analytics_router)
app.include_router(ws_router)
app.include_router(chat_history_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=settings.is_dev)
