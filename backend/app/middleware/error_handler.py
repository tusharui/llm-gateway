from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

from app.redact import redact, redact_error

logger = logging.getLogger("gateway")

# FastAPI installs its own handler for RequestValidationError, and that handler
# runs *inside* the middleware stack -- so a 422 produced by request validation
# never reaches ErrorHandlerMiddleware and would otherwise keep the old
# `{"detail": [...]}` shape. Registering our own handler on the app is the only
# way to bring it into the same envelope as every other error.
#
# Kept here rather than in main.py so the envelope and the handler that emits
# it cannot drift apart.
async def validation_exception_handler(request: Request, exc: Exception):
    request_id = _request_id_of(request)
    details = exc.errors() if hasattr(exc, "errors") else None
    return JSONResponse(
        status_code=422,
        content=error_body(
            status_code=422,
            message=_summarise_validation(details),
            error_type="INVALID_REQUEST",
            request_id=request_id,
            # Strip the echoed `input` before it reaches the client. Pydantic
            # includes the offending value, which reflects arbitrary
            # caller-supplied text straight back in the response and into
            # whatever logs it. The field path and the reason are the useful
            # parts; the value is the caller's own and they already have it.
            details=_strip_echoed_input(details),
        ),
        headers={"X-Request-ID": request_id} if request_id else None,
    )


_ECHOED_FIELDS = ("input", "ctx")


def _strip_echoed_input(details: list | None) -> list | None:
    if not details:
        return details
    cleaned = []
    for item in details:
        if isinstance(item, dict):
            cleaned.append({k: v for k, v in item.items() if k not in _ECHOED_FIELDS})
        else:
            cleaned.append(item)
    return cleaned


async def http_exception_handler(request: Request, exc: Exception):
    """Same envelope for HTTPException raised inside a route.

    Starlette catches these before the middleware sees them, so without an
    app-level handler a 404 or a deliberate HTTPException kept the old
    `{"detail": ...}` shape while everything else used `{"error": ...}`.
    """
    status_code = getattr(exc, "status_code", 500)
    detail = getattr(exc, "detail", None)
    request_id = _request_id_of(request)

    # A 401 raised by AuthMiddleware arrives as an HTTPException; its detail
    # is a plain string. A validation-style list detail is handled the same way
    # as in validation_exception_handler so both routes produce one shape.
    if isinstance(detail, str):
        message, details = detail, None
    else:
        message, details = _summarise_validation(detail), detail

    return JSONResponse(
        status_code=status_code,
        content=error_body(
            status_code=status_code,
            message=message,
            error_type=_ERROR_TYPE_BY_STATUS.get(status_code, f"HTTP_{status_code}"),
            request_id=request_id,
            details=details,
        ),
        headers={**(getattr(exc, "headers", None) or {}), "X-Request-ID": request_id}
        if request_id
        else getattr(exc, "headers", None),
    )


def error_body(
    *,
    status_code: int,
    message: str,
    error_type: str,
    request_id: str | None,
    details: list | None = None,
) -> dict:
    """One error envelope for every failure the API returns.

    The gateway previously had three incompatible shapes:

      * ``{"error": {...}}``        generic exceptions
      * ``{"detail": "..."}``       HTTPException
      * ``{"detail": [{...}]}``     FastAPI request validation

    A client could not write one error path, because "is it under detail or
    error?" depended on how the server happened to fail. Everything is now
    under ``error``, with ``details`` carrying structured validation
    information, and ``type`` naming the class of failure so a client can
    branch without string-matching the message.

    The envelope deliberately mirrors the OpenAI chat-completions shape, since
    that is the API this gateway is emulating. A client written against
    OpenAI already knows how to read it.
    """
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
            "request_id": request_id,
            **({"details": details} if details else {}),
        }
    }


# Maps an HTTP status onto the error class a client should branch on.
_ERROR_TYPE_BY_STATUS = {
    400: "INVALID_REQUEST",
    401: "AUTHENTICATION_ERROR",
    403: "PERMISSION_ERROR",
    404: "NOT_FOUND",
    422: "INVALID_REQUEST",
    429: "RATE_LIMIT_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _request_id_of(request) -> str | None:
    return getattr(request.state, "request_id", None)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)

        except StarletteHTTPException as e:
            request_id = _request_id_of(request)
            # FastAPI's own validation errors arrive here as an HTTPException
            # whose detail is a list of pydantic error dicts. Flatten them
            # into `details` so the client still gets per-field information
            # without the envelope changing shape.
            detail = e.detail
            details = None
            message = detail if isinstance(detail, str) else None
            if message is None:
                details = detail
                message = _summarise_validation(details)

            return JSONResponse(
                status_code=e.status_code,
                content=error_body(
                    status_code=e.status_code,
                    message=message,
                    error_type=_ERROR_TYPE_BY_STATUS.get(
                        e.status_code, f"HTTP_{e.status_code}"
                    ),
                    request_id=request_id,
                    details=details,
                ),
                headers={**(e.headers or {}), "X-Request-ID": request_id}
                if request_id
                else e.headers,
            )

        except Exception as e:
            request_id = _request_id_of(request)
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
                content=error_body(
                    status_code=500,
                    message=message,
                    error_type="INTERNAL_ERROR",
                    request_id=request_id,
                ),
                headers={"X-Request-ID": request_id} if request_id else None,
            )


def _summarise_validation(details: list) -> str:
    """A one-line summary of a pydantic error list.

    The per-field errors are not discarded -- they are passed through as
    `details` -- but `message` must still be a string, because that is what
    every OpenAI-shaped client reads first.
    """
    if not details:
        return "Request validation failed"
    fields = []
    for item in details[:3]:
        if isinstance(item, dict):
            loc = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            fields.append(f"{loc}: {item.get('msg')}" if loc else str(item.get("msg")))
    head = "; ".join(f for f in fields if f)
    extra = len(details) - 3
    if extra > 0:
        head = f"{head} (+{extra} more)"
    return head or "Request validation failed"
