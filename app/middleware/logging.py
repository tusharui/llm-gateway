from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import time
import logging

logger = logging.getLogger("gateway")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        method = request.method
        path = request.url.path

        response = await call_next(request)

        duration = (time.perf_counter() - start) * 1000
        status = response.status_code
        api_key_id = getattr(request.state, "api_key_id", "anonymous")

        logger.info(
            "request",
            extra={
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round(duration),
                "api_key_id": api_key_id,
            },
        )
        return response
