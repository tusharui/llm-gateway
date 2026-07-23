import uuid
import asyncio
from typing import Dict, Any
from app.engine.router import route_chat
from app.schemas import ChatRequest

_queue: Dict[str, dict] = {}
_concurrency = 5
_active = 0


def enqueue(payload: ChatRequest) -> str:
    job_id = str(uuid.uuid4())
    _queue[job_id] = {
        "id": job_id,
        "type": "chat",
        "payload": payload.model_dump(),
        "status": "pending",
        "result": None,
        "error": None,
    }
    asyncio.get_event_loop().create_task(_process_queue())
    return job_id


def get_job(job_id: str) -> dict | None:
    return _queue.get(job_id)


def get_queue_status() -> dict:
    return {
        "total": len(_queue),
        "pending": sum(1 for j in _queue.values() if j["status"] == "pending"),
        "processing": _active,
        "completed": sum(1 for j in _queue.values() if j["status"] == "completed"),
        "failed": sum(1 for j in _queue.values() if j["status"] == "failed"),
    }


async def _process_queue():
    global _active
    if _active >= _concurrency:
        return

    job = next((j for j in _queue.values() if j["status"] == "pending"), None)
    if not job:
        return

    _active += 1
    job["status"] = "processing"

    try:
        req = ChatRequest(**job["payload"])
        result, _ = await route_chat(req)
        job["result"] = result.model_dump()
        job["status"] = "completed"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
    finally:
        _active -= 1
        asyncio.get_event_loop().create_task(_process_queue())
