import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, desc
from datetime import datetime, timezone
from app.database import get_session
from app.models import ChatSession, ChatMessage

router = APIRouter(prefix="/chat-history")


class CreateSessionRequest(BaseModel):
    title: str = "New chat"
    provider: str = "groq"
    model: str = "llama-3.3-70b-versatile"


class AddMessageRequest(BaseModel):
    role: str
    content: str


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None


@router.get("/sessions")
async def list_sessions():
    session = await get_session()
    if not session:
        return {"sessions": []}

    try:
        result = await session.execute(
            select(ChatSession).order_by(desc(ChatSession.updated_at))
        )
        sessions = result.scalars().all()
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "provider": s.provider,
                    "model": s.model,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in sessions
            ]
        }
    finally:
        await session.close()


@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        new_session = ChatSession(
            id=str(uuid.uuid4()),
            title=req.title,
            provider=req.provider,
            model=req.model,
        )
        session.add(new_session)
        await session.commit()
        return {
            "id": new_session.id,
            "title": new_session.title,
            "provider": new_session.provider,
            "model": new_session.model,
            "created_at": new_session.created_at.isoformat() if new_session.created_at else None,
            "updated_at": new_session.updated_at.isoformat() if new_session.updated_at else None,
        }
    finally:
        await session.close()


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        chat_session = result.scalar_one_or_none()
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")

        msg_result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        messages = msg_result.scalars().all()

        return {
            "id": chat_session.id,
            "title": chat_session.title,
            "provider": chat_session.provider,
            "model": chat_session.model,
            "created_at": chat_session.created_at.isoformat() if chat_session.created_at else None,
            "updated_at": chat_session.updated_at.isoformat() if chat_session.updated_at else None,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages
            ],
        }
    finally:
        await session.close()


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, req: UpdateSessionRequest):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        chat_session = result.scalar_one_or_none()
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")

        if req.title is not None:
            chat_session.title = req.title
        if req.provider is not None:
            chat_session.provider = req.provider
        if req.model is not None:
            chat_session.model = req.model
        chat_session.updated_at = datetime.now(timezone.utc)

        await session.commit()
        return {"message": "Session updated"}
    finally:
        await session.close()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        await session.execute(
            delete(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        await session.execute(
            delete(ChatSession).where(ChatSession.id == session_id)
        )
        await session.commit()
        return {"message": "Session deleted"}
    finally:
        await session.close()


@router.post("/sessions/{session_id}/messages")
async def add_message(session_id: str, req: AddMessageRequest):
    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        result = await session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        chat_session = result.scalar_one_or_none()
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")

        msg = ChatMessage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=req.role,
            content=req.content,
        )
        session.add(msg)

        chat_session.updated_at = datetime.now(timezone.utc)
        if req.role == "user":
            msg_result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .where(ChatMessage.role == "user")
            )
            if len(msg_result.scalars().all()) == 0:
                chat_session.title = req.content[:50] + ("..." if len(req.content) > 50 else "")

        await session.commit()
        return {"message": "Message added"}
    finally:
        await session.close()
