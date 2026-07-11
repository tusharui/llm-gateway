from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import secrets
import hashlib
from app.database import db_fetchone, db_fetchall, db_execute

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

    await db_execute(
        """INSERT INTO api_keys (id, key_prefix, key_hash, name, is_active, rate_limit_max, rate_limit_window_ms)
           VALUES ($1, $2, $3, $4, TRUE, $5, $6)""",
        (key_id, key_prefix, key_hash, req.name, req.rate_limit_max, req.rate_limit_window_ms),
    )

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
    rows = await db_fetchall(
        "SELECT id, key_prefix, name, is_active, rate_limit_max, rate_limit_window_ms, created_at, last_used_at FROM api_keys ORDER BY created_at DESC"
    )
    return {"keys": [dict(r) for r in rows]}


@router.get("/{key_id}")
async def get_key(key_id: str):
    row = await db_fetchone(
        "SELECT id, key_prefix, name, is_active, rate_limit_max, rate_limit_window_ms, created_at, last_used_at FROM api_keys WHERE id = $1",
        (key_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    return dict(row)


@router.patch("/{key_id}")
async def update_key(key_id: str, req: UpdateKeyRequest):
    existing = await db_fetchone("SELECT id FROM api_keys WHERE id = $1", (key_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="API key not found")

    updates = []
    params = []
    idx = 1

    if req.name is not None:
        updates.append(f"name = ${idx}")
        params.append(req.name)
        idx += 1
    if req.is_active is not None:
        updates.append(f"is_active = ${idx}")
        params.append(req.is_active)
        idx += 1
    if req.rate_limit_max is not None:
        updates.append(f"rate_limit_max = ${idx}")
        params.append(req.rate_limit_max)
        idx += 1
    if req.rate_limit_window_ms is not None:
        updates.append(f"rate_limit_window_ms = ${idx}")
        params.append(req.rate_limit_window_ms)
        idx += 1

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(key_id)
    await db_execute(
        f"UPDATE api_keys SET {', '.join(updates)} WHERE id = ${idx}",
        tuple(params),
    )

    return {"message": "Key updated"}


@router.delete("/{key_id}")
async def delete_key(key_id: str):
    existing = await db_fetchone("SELECT id FROM api_keys WHERE id = $1", (key_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="API key not found")
    await db_execute("DELETE FROM api_keys WHERE id = $1", (key_id,))
    return {"message": "Key deleted"}


@router.post("/{key_id}/toggle")
async def toggle_key(key_id: str):
    row = await db_fetchone("SELECT id, is_active FROM api_keys WHERE id = $1", (key_id,))
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    new_state = not row["is_active"]
    await db_execute("UPDATE api_keys SET is_active = $1 WHERE id = $2", (new_state, key_id))
    return {"id": key_id, "is_active": new_state}
