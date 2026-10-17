"""Dashboard user authentication: register, login, logout, current user.

Separate from the gateway's API-key auth in middleware/auth.py. A key
authenticates a program via bearer token; this authenticates a person via an
HttpOnly cookie. Neither substitutes for the other, so a leaked session does
not open /chat and a leaked key does not mint a session.

/auth is intentionally absent from AUTH_PREFIXES, because a login endpoint
that requires a credential is not a login endpoint. These four routes are the
only public surface in the app, so no response here echoes a password hash or
a session token in the body: the token travels in an HttpOnly cookie.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import get_session
from app.models import AuthSession
from app.security import (
    create_session,
    current_user,
    enforce_auth_rate_limit,
    hash_password,
    hash_token,
    needs_rehash,
    normalize_email,
    purge_expired_sessions,
    verify_login,
    verify_password,
)

router = APIRouter(prefix="/auth")
logger = logging.getLogger("gateway.auth")

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )

    @field_validator("email")
    @classmethod
    def _must_look_like_an_email(cls, value: str) -> str:
        candidate = value.strip()
        if candidate.count("@") != 1 or " " in candidate:
            raise ValueError("Invalid email address")
        local, _, domain = candidate.partition("@")
        if not local or not domain or "." not in domain:
            raise ValueError("Invalid email address")
        return candidate


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )


def public_user(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _issue_session(session, user, response: Response) -> None:
    raw_token = await create_session(session, user.id)
    await purge_expired_sessions(session, user.id)
    await session.commit()

    response.set_cookie(
        value=raw_token,
        key=settings.session_cookie_name,
        httponly=True,
        secure=not settings.is_dev,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request, response: Response):
    enforce_auth_rate_limit(request)

    if not settings.allow_registration:
        raise HTTPException(
            status_code=403,
            detail=(
                "Registration is disabled. Ask an administrator to create your "
                "account, or run: python -m scripts.create_user"
            ),
        )

    email = normalize_email(req.email)
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from app.models import User

    try:
        existing = await session.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Email is already registered")

        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=hash_password(req.password),
            is_active=True,
            is_admin=False,
        )
        session.add(user)
        await session.flush()
        await _issue_session(session, user, response)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Email is already registered")
    finally:
        await session.close()

    _no_store(response)
    logger.info("user registered")
    return {"user": public_user(user)}


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    enforce_auth_rate_limit(request)

    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        user = await verify_login(session, req.email, req.password)

        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(req.password)

        user.last_login_at = datetime.now(timezone.utc)
        await _issue_session(session, user, response)
    finally:
        await session.close()

    _no_store(response)
    return {"user": public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response, user=Depends(current_user)):
    raw_token = request.cookies.get(settings.session_cookie_name)

    session = await get_session()
    if session:
        try:
            if raw_token:
                await session.execute(
                    delete(AuthSession).where(
                        AuthSession.token_hash == hash_token(raw_token)
                    )
                )
                await session.commit()
        finally:
            await session.close()

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=not settings.is_dev,
    )
    _no_store(response)
    return {"message": "Signed out"}


@router.get("/me")
async def me(response: Response, user=Depends(current_user)):
    _no_store(response)
    return {"user": public_user(user)}


@router.post("/change-password")
async def change_password(
    req: PasswordChangeRequest, request: Request, response: Response,
    user=Depends(current_user),
):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        if not verify_password(req.current_password, user.password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        user.password_hash = hash_password(req.new_password)

        raw_token = request.cookies.get(settings.session_cookie_name)
        stmt = delete(AuthSession).where(AuthSession.user_id == user.id)
        if raw_token:
            stmt = stmt.where(AuthSession.token_hash != hash_token(raw_token))
        await session.execute(stmt)
        await session.commit()
    finally:
        await session.close()

    _no_store(response)
    return {"message": "Password updated; other sessions signed out"}