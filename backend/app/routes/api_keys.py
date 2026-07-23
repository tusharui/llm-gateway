from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import secrets
import hashlib
from sqlalchemy import select, update, delete
from app.database import get_session
from app.models import ApiKey

router = APIRouter(prefix="/api-keys")


def hash_string(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    raw_key = f"sk-gw-{secrets.token_urlsafe(32)}"
    key_hash = hash_string(raw_key)
    key_prefix = raw_key[:12]
    return raw_key, key_hash, key_prefix


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    rate_limit_max: int = Field(default=60, ge=1, le=10000)
    rate_limit_window_ms: int = Field(default=60000, ge=1000, le=3600000)


class UpdateKeyRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    is_active: Optional[bool] = None
    rate_limit_max: Optional[int] = Field(None, ge=1, le=10000)
    rate_limit_window_ms: Optional[int] = Field(None, ge=1000, le=3600000)


@router.post("")
async def create_key(req: CreateKeyRequest):
    raw_key, key_hash, key_prefix = generate_api_key()
    key_id = str(uuid.uuid4())

    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        api_key = ApiKey(
            id=key_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            name=req.name,
            is_active=True,
            rate_limit_max=req.rate_limit_max,
            rate_limit_window_ms=req.rate_limit_window_ms,
        )
        session.add(api_key)
        await session.commit()
    finally:
        await session.close()

    return {
        "id": key_id,
        "key": raw_key,
        "key_prefix": key_prefix,
        "name": req.name,
        "is_active": True,
        "rate_limit_max": req.rate_limit_max,
        "rate_limit_window_ms": req.rate_limit_window_ms,
        "message": "Save the key now — it won't be shown again.",
    }


@router.get("")
async def list_keys():
    session = await get_session()
    if not session:
        return {"keys": []}

    try:
        result = await session.execute(
            select(ApiKey).order_by(ApiKey.created_at.desc())
        )
        keys = result.scalars().all()
        return {
            "keys": [
                {
                    "id": k.id,
                    "key_prefix": k.key_prefix,
                    "name": k.name,
                    "is_active": k.is_active,
                    "rate_limit_max": k.rate_limit_max,
                    "rate_limit_window_ms": k.rate_limit_window_ms,
                    "created_at": k.created_at,
                    "last_used_at": k.last_used_at,
                }
                for k in keys
            ]
        }
    finally:
        await session.close()


@router.get("/{key_id}")
async def get_key(key_id: str):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        return {
            "id": api_key.id,
            "key_prefix": api_key.key_prefix,
            "name": api_key.name,
            "is_active": api_key.is_active,
            "rate_limit_max": api_key.rate_limit_max,
            "rate_limit_window_ms": api_key.rate_limit_window_ms,
            "created_at": api_key.created_at,
            "last_used_at": api_key.last_used_at,
        }
    finally:
        await session.close()


@router.patch("/{key_id}")
async def update_key(key_id: str, req: UpdateKeyRequest):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")

        updates = {}
        if req.name is not None:
            updates[ApiKey.name] = req.name
        if req.is_active is not None:
            updates[ApiKey.is_active] = req.is_active
        if req.rate_limit_max is not None:
            updates[ApiKey.rate_limit_max] = req.rate_limit_max
        if req.rate_limit_window_ms is not None:
            updates[ApiKey.rate_limit_window_ms] = req.rate_limit_window_ms

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(**updates)
        )
        await session.commit()
        return {"message": "Key updated"}
    finally:
        await session.close()


@router.delete("/{key_id}")
async def delete_key(key_id: str):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        await session.execute(delete(ApiKey).where(ApiKey.id == key_id))
        await session.commit()
        return {"message": "Key deleted"}
    finally:
        await session.close()


@router.post("/{key_id}/toggle")
async def toggle_key(key_id: str):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
        api_key = result.scalar_one_or_none()
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")

        new_state = not api_key.is_active
        await session.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(is_active=new_state)
        )
        await session.commit()
        return {"id": key_id, "is_active": new_state}
    finally:
        await session.close()
