import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.engine.router import route_chat_stream
from app.engine.failover import route_with_failover
from app.schemas import ChatRequest, ChatMessage

router = APIRouter()


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                messages = [ChatMessage(**m) for m in data.get("messages", [])]
                if not messages:
                    await websocket.send_json({"error": "No messages provided"})
                    continue

                req = ChatRequest(
                    model=data.get("model", "llama-3.3-70b-versatile"),
                    messages=messages,
                    stream=True,
                    temperature=data.get("temperature", 0.7),
                    max_tokens=data.get("max_tokens"),
                    top_p=data.get("top_p"),
                )

                try:
                    async for chunk in route_chat_stream(req):
                        await websocket.send_json(chunk.model_dump())
                except Exception:
                    result, provider = await route_with_failover(req)
                    await websocket.send_json(result.model_dump())

                await websocket.send_json({"done": True})

            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON"})
            except Exception as e:
                await websocket.send_json({"error": str(e)})

    except WebSocketDisconnect:
        pass
