from fastapi import APIRouter, Request
from app.schemas import ChatRequest
from app.queue.job_queue import enqueue, get_job, get_queue_status

router = APIRouter(prefix="/batch")


@router.post("/chat")
async def batch_chat(req: ChatRequest, request: Request):
    job_id = enqueue(req)
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = get_job(job_id)
    if not job:
        return {"error": "Job not found"}
    return {
        "id": job["id"],
        "type": job["type"],
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }


@router.get("/queue/status")
async def queue_status():
    return get_queue_status()
