from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

from app.redact import redact, redact_error

logger = logging.getLogger("gateway")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except StarletteHTTPException as e:
            request_id = getattr(request.state, "request_id", None)
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail, "request_id": request_id},
                headers={**(e.headers or {}), "X-Request-ID": request_id} if request_id else e.headers,
            )
        except Exception as e:
            request_id = getattr(request.state, "request_id", None)
            message = redact_error(e)
            logger.error(
                "unhandled_error",
                extra={
                    "error_type": type(e).__name__,
                    "error_message": message,
                    "request_id": request_id,
                    "path": request.url.path,
                },
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "type": "INTERNAL_ERROR",
                        "message": message,
                        "request_id": request_id,
                    }
                },
                headers={"X-Request-ID": request_id} if request_id else None,
            )
