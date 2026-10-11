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
    """Record one provider failure and open the breaker at `threshold`.

    The cooldown used to schedule a fresh timer task on every failure at or
    past the threshold. Under sustained failure that is one task per request,
    each sleeping for the full cooldown and each holding the breaker alive as a
    referenced closure: 12 failures left 8 pending tasks in a 12-failure run.
    A provider that stays down therefore leaked tasks in proportion to traffic,
    and there was no cancellation path because nothing kept a handle.

    One timer per open->half-open transition instead, and only while the
    breaker is actually open. Repeated failures against an already-open
    breaker re-arm the same single timer.
    """
    b = get_breaker_state(provider)
    b["failures"] += 1
    b["last_failure_time"] = time.time() * 1000

    if b["failures"] >= threshold:
        b["state"] = "open"
        _arm_cooldown(b, cooldown_ms)


def _arm_cooldown(b: dict, cooldown_ms: int) -> None:
    """(Re)arm the single half-open timer for this breaker."""
    existing = b.pop("_cooldown_task", None)
    if existing is not None and not existing.done():
        existing.cancel()

    async def _set_half_open():
        try:
            await asyncio.sleep(cooldown_ms / 1000)
            # A success during the cooldown already closed the breaker and
            # cancelled this task; if we are still running, no success landed.
            b["state"] = "half-open"
            b.pop("_cooldown_task", None)
        except asyncio.CancelledError:
            raise

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (sync caller, e.g. a test or a management script). The
        # breaker stays open; is_allowed() keeps returning False, which is
        # the safe direction. Previously this raised out of record_failure
        # and turned a provider failure into an unrelated crash.
        return

    b["_cooldown_task"] = loop.create_task(_set_half_open())


def reset_all() -> None:
    """Clear every breaker and cancel pending cooldown timers.

    Exists for tests and for an operator-triggered reset. Without it a test
    that opened a breaker leaves the module-level dict dirty for the next
    test, and a long cooldown timer survives the process that started it.
    """
    for state in list(_breakers.values()):
        task = state.pop("_cooldown_task", None)
        if task is not None and not task.done():
            task.cancel()
    _breakers.clear()


def is_allowed(provider: str) -> bool:
    b = get_breaker_state(provider)
    return b["state"] in ("closed", "half-open")
