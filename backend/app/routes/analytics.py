from fastapi import APIRouter, Query
from sqlalchemy import select, func, case, cast, Date
from datetime import datetime, timezone, timedelta
from app.database import get_session
from app.models import UsageRecord

router = APIRouter(prefix="/analytics")


def _days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/summary")
async def usage_summary(days: int = Query(default=7, ge=1, le=90)):
    session = await get_session()
    if not session:
        return {}

    try:
        since = _days_ago(days)
        result = await session.execute(
            select(
                func.count(UsageRecord.id).label("total_requests"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0).label("total_cost"),
                func.coalesce(func.avg(UsageRecord.latency_ms), 0).label("avg_latency"),
                func.coalesce(
                    func.sum(case((UsageRecord.success == True, 1), else_=0)), 0
                ).label("successful"),
                func.coalesce(
                    func.sum(case((UsageRecord.success == False, 1), else_=0)), 0
                ).label("failed"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == True, 1), else_=0)), 0
                ).label("cached"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == True, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("cache_savings_usd"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == False, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("actual_cost_usd"),
            ).where(UsageRecord.timestamp > since)
        )
        row = result.one()
        total_requests = int(row.total_requests)
        cached = int(row.cached)
        return {
            "total_requests": total_requests,
            "total_tokens": int(row.total_tokens),
            "total_cost": float(row.actual_cost_usd),
            "cache_savings_usd": float(row.cache_savings_usd),
            "cache_hit_rate": round(cached / total_requests, 4) if total_requests else 0.0,
            "avg_latency": int(row.avg_latency),
            "successful": int(row.successful),
            "failed": int(row.failed),
            "cached": cached,
        }
    finally:
        await session.close()


@router.get("/by-provider")
async def usage_by_provider(days: int = Query(default=7, ge=1, le=90)):
    session = await get_session()
    if not session:
        return {"providers": []}

    try:
        since = _days_ago(days)
        result = await session.execute(
            select(
                UsageRecord.provider,
                func.count(UsageRecord.id).label("requests"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("tokens"),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0).label("cost"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == False, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("actual_cost"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == True, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("saved_cost"),
                func.coalesce(func.avg(UsageRecord.latency_ms), 0).label("avg_latency"),
                func.coalesce(
                    func.sum(case((UsageRecord.success == True, 1), else_=0)), 0
                ).label("successful"),
            )
            .where(UsageRecord.timestamp > since)
            .group_by(UsageRecord.provider)
            .order_by(func.sum(UsageRecord.cost_usd).desc())
        )
        rows = result.all()
        return {
            "providers": [
                {
                    "provider": r.provider,
                    "requests": r.requests,
                    "tokens": int(r.tokens),
                    "cost": float(r.actual_cost),
                    "saved_cost": float(r.saved_cost),
                    "avg_latency": int(r.avg_latency),
                    "successful": int(r.successful),
                }
                for r in rows
            ]
        }
    finally:
        await session.close()


@router.get("/by-model")
async def usage_by_model(days: int = Query(default=7, ge=1, le=90)):
    session = await get_session()
    if not session:
        return {"models": []}

    try:
        since = _days_ago(days)
        result = await session.execute(
            select(
                UsageRecord.model,
                UsageRecord.provider,
                func.count(UsageRecord.id).label("requests"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("tokens"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == False, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("actual_cost"),
                func.coalesce(
                    func.sum(case((UsageRecord.cached == True, UsageRecord.cost_usd), else_=0)), 0.0
                ).label("saved_cost"),
                func.coalesce(func.avg(UsageRecord.latency_ms), 0).label("avg_latency"),
            )
            .where(UsageRecord.timestamp > since)
            .group_by(UsageRecord.model, UsageRecord.provider)
            .order_by(func.count(UsageRecord.id).desc())
        )
        rows = result.all()
        return {
            "models": [
                {
                    "model": r.model,
                    "provider": r.provider,
                    "requests": r.requests,
                    "tokens": int(r.tokens),
                    "cost": float(r.actual_cost),
                    "saved_cost": float(r.saved_cost),
                    "avg_latency": int(r.avg_latency),
                }
                for r in rows
            ]
        }
    finally:
        await session.close()


@router.get("/timeline")
async def usage_timeline(days: int = Query(default=7, ge=1, le=90)):
    session = await get_session()
    if not session:
        return {"timeline": []}

    try:
        since = _days_ago(days)
        result = await session.execute(
            select(
                cast(UsageRecord.timestamp, Date).label("day"),
                func.count(UsageRecord.id).label("requests"),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("tokens"),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0).label("cost"),
                func.coalesce(func.avg(UsageRecord.latency_ms), 0).label("avg_latency"),
            )
            .where(UsageRecord.timestamp > since)
            .group_by(cast(UsageRecord.timestamp, Date))
            .order_by(cast(UsageRecord.timestamp, Date))
        )
        rows = result.all()
        return {
            "timeline": [
                {
                    "day": str(r.day),
                    "requests": r.requests,
                    "tokens": int(r.tokens),
                    "cost": float(r.cost),
                    "avg_latency": int(r.avg_latency),
                }
                for r in rows
            ]
        }
    finally:
        await session.close()


@router.get("/recent")
async def recent_requests(limit: int = Query(default=50, ge=1, le=500)):
    session = await get_session()
    if not session:
        return {"requests": []}

    try:
        result = await session.execute(
            select(UsageRecord).order_by(UsageRecord.timestamp.desc()).limit(limit)
        )
        records = result.scalars().all()
        return {
            "requests": [
                {
                    "id": r.id,
                    "api_key_id": r.api_key_id,
                    "provider": r.provider,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "cost_usd": r.cost_usd,
                    "latency_ms": r.latency_ms,
                    "success": r.success,
                    "cached": r.cached,
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                }
                for r in records
            ]
        }
    finally:
        await session.close()
