import asyncio
import random
from typing import Callable, Awaitable, TypeVar

T = TypeVar("T")


def _is_retryable(err: Exception) -> bool:
    status_code = getattr(getattr(err, "response", None), "status_code", None)
    if status_code is None:
        status_code = getattr(err, "status_code", None)
    if status_code is None:
        return True
    if status_code in (408, 409, 425, 429):
        return True
    return status_code >= 500


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    retries: int = 3,
    base_delay_ms: int = 1000,
    max_delay_ms: int = 30000,
) -> T:
    last_error = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as err:
            last_error = err
            # 4xx (bad model, bad key, quota) will not fix itself — fail over now
            # instead of sleeping through the whole backoff ladder.
            if not _is_retryable(err):
                raise
            if attempt == retries:
                break
            delay = min(base_delay_ms * (2 ** attempt), max_delay_ms)
            jitter = delay * (0.5 + random.random() * 0.5)
            await asyncio.sleep(jitter / 1000)
    raise last_error
