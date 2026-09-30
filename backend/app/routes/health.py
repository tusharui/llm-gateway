"""Liveness and readiness.

Two endpoints with deliberately different jobs, because conflating them is how
a broken instance ends up serving production traffic:

* ``/health`` is liveness. It answers "is this process running?" and touches
  nothing external. It must stay fast, because orchestrators poll it every few
  seconds and a slow healthcheck turns into cascading timeouts.
* ``/ready`` is readiness. It answers "should this instance receive traffic?"
  and checks the database, because every meaningful endpoint in this service
  needs one.

The previous single ``/health`` endpoint reported provider health but never
checked the database at all. With the database pointed at an unreachable host
it still returned 200 "healthy", so a bad deploy passed its healthcheck and
received 100% of traffic while every database-backed route failed.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.database import db_status
from app.providers.registry import get_available_providers

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness. No database, no provider calls, no I/O of consequence."""
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def ready(response: Response):
    """Readiness. 503 unless the instance can actually serve traffic."""
    db = await db_status()

    if not db["ok"]:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": db,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    providers = get_available_providers()
    checks = []
    for p in providers:
        try:
            result = await p.health_check()
            checks.append(
                {
                    "provider": p.name,
                    "status": "healthy" if result["ok"] else "degraded",
                    "latency_ms": result["latency_ms"],
                }
            )
        except Exception as e:
            checks.append(
                {"provider": p.name, "status": "error", "error": str(e)}
            )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "database": db,
            "providers": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )