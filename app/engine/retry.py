import asyncio
import random
from typing import Callable, Awaitable, TypeVar

T = TypeVar("T")


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
            if attempt == retries:
                break
            delay = min(base_delay_ms * (2 ** attempt), max_delay_ms)
            jitter = delay * (0.5 + random.random() * 0.5)
            await asyncio.sleep(jitter / 1000)
    raise last_error
