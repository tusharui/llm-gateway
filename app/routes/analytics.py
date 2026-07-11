from fastapi import APIRouter, Query
from app.database import db_fetchall, db_fetchone

router = APIRouter(prefix="/analytics")


@router.get("/summary")
async def usage_summary(days: int = Query(default=7, ge=1, le=90)):
    total = await db_fetchone(
        """SELECT COUNT(*) as total_requests,
                  COALESCE(SUM(total_tokens), 0) as total_tokens,
                  COALESCE(SUM(cost_usd), 0) as total_cost,
                  COALESCE(AVG(latency_ms), 0) as avg_latency,
                  COALESCE(SUM(CASE WHEN success THEN 1 ELSE 0 END), 0) as successful,
                  COALESCE(SUM(CASE WHEN NOT success THEN 1 ELSE 0 END), 0) as failed,
                  COALESCE(SUM(CASE WHEN cached THEN 1 ELSE 0 END), 0) as cached
           FROM usage_records
           WHERE timestamp > NOW() - INTERVAL '1 day' * $1""",
        (days,),
    )
    return dict(total) if total else {}


@router.get("/by-provider")
async def usage_by_provider(days: int = Query(default=7, ge=1, le=90)):
    rows = await db_fetchall(
        """SELECT provider,
                  COUNT(*) as requests,
                  COALESCE(SUM(total_tokens), 0) as tokens,
                  COALESCE(SUM(cost_usd), 0) as cost,
                  COALESCE(AVG(latency_ms), 0) as avg_latency,
                  COALESCE(SUM(CASE WHEN success THEN 1 ELSE 0 END), 0) as successful
           FROM usage_records
           WHERE timestamp > NOW() - INTERVAL '1 day' * $1
           GROUP BY provider
           ORDER BY cost DESC""",
        (days,),
    )
    return {"providers": [dict(r) for r in rows]}


@router.get("/by-model")
async def usage_by_model(days: int = Query(default=7, ge=1, le=90)):
    rows = await db_fetchall(
        """SELECT model, provider,
                  COUNT(*) as requests,
                  COALESCE(SUM(total_tokens), 0) as tokens,
                  COALESCE(SUM(cost_usd), 0) as cost,
                  COALESCE(AVG(latency_ms), 0) as avg_latency
           FROM usage_records
           WHERE timestamp > NOW() - INTERVAL '1 day' * $1
           GROUP BY model, provider
           ORDER BY requests DESC""",
        (days,),
    )
    return {"models": [dict(r) for r in rows]}


@router.get("/timeline")
async def usage_timeline(days: int = Query(default=7, ge=1, le=90)):
    rows = await db_fetchall(
        """SELECT DATE(timestamp) as day,
                  COUNT(*) as requests,
                  COALESCE(SUM(total_tokens), 0) as tokens,
                  COALESCE(SUM(cost_usd), 0) as cost,
                  COALESCE(AVG(latency_ms), 0) as avg_latency
           FROM usage_records
           WHERE timestamp > NOW() - INTERVAL '1 day' * $1
           GROUP BY DATE(timestamp)
           ORDER BY day""",
        (days,),
    )
    return {"timeline": [dict(r) for r in rows]}


@router.get("/recent")
async def recent_requests(limit: int = Query(default=50, ge=1, le=500)):
    rows = await db_fetchall(
        """SELECT id, api_key_id, provider, model, prompt_tokens, completion_tokens,
                  total_tokens, cost_usd, latency_ms, success, cached, timestamp
           FROM usage_records
           ORDER BY timestamp DESC
           LIMIT $1""",
        (limit,),
    )
    return {"requests": [dict(r) for r in rows]}
