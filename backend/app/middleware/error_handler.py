from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import traceback

logger = logging.getLogger("gateway")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            request_id = getattr(request.state, "request_id", None)
            logger.error(
                "unhandled_error",
                extra={
                    "error_type": type(e).__name__,
                    "message": str(e),
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
                        "message": str(e),
                        "request_id": request_id,
                    }
                },
                headers={"X-Request-ID": request_id} if request_id else None,
            )
