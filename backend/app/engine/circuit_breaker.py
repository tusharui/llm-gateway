import asyncio
import time
from typing import Dict

_breakers: Dict[str, dict] = {}


def get_breaker_state(provider: str) -> dict:
    if provider not in _breakers:
        _breakers[provider] = {
            "failures": 0,
            "last_failure_time": 0,
            "state": "closed",
        }
    return _breakers[provider]


def record_success(provider: str):
    b = get_breaker_state(provider)
    b["failures"] = 0
    b["state"] = "closed"


def record_failure(provider: str, threshold: int, cooldown_ms: int):
    b = get_breaker_state(provider)
    b["failures"] += 1
    b["last_failure_time"] = time.time() * 1000

    if b["failures"] >= threshold:
        b["state"] = "open"

        async def _set_half_open():
            await asyncio.sleep(cooldown_ms / 1000)
            b["state"] = "half-open"

        asyncio.get_event_loop().create_task(_set_half_open())


def is_allowed(provider: str) -> bool:
    b = get_breaker_state(provider)
    return b["state"] in ("closed", "half-open")
