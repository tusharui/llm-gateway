import time
import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("gateway")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        method = request.method
        path = request.url.path

        request_id = request.headers.get("x-request-id")
        if not request_id:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id

        response = await call_next(request)

        duration = (time.perf_counter() - start) * 1000
        status = response.status_code
        api_key_id = getattr(request.state, "api_key_id", "anonymous")

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-MS"] = str(round(duration))

        logger.info(
            "request",
            extra={
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": round(duration),
                "api_key_id": api_key_id,
                "request_id": request_id,
            },
        )
        return response
