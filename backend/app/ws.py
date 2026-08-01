"""WebSocket subscription router using Redis Pub/Sub per connection.

LIFECYCLE DESIGN:
  Per-connection Redis subscription ensures independent lifecycle management and
  automatic cleanup when a WebSocket client disconnects.
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


async def handle_ws_subscription(websocket: WebSocket, run_id: uuid.UUID, redis_client: Redis) -> None:
    """Subscribe a WebSocket connection to Redis channel nexus:events:run:{run_id} and stream events."""
    await websocket.accept()
    pubsub = redis_client.pubsub()
    channel = f"nexus:events:run:{run_id}"

    try:
        await pubsub.subscribe(channel)

        async def listen_redis():
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    payload_str = message.get("data")
                    if payload_str:
                        await websocket.send_text(payload_str)

        async def font_listen_task():
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                pass

        listener_task = asyncio.create_task(listen_redis())
        font_task = asyncio.create_task(font_listen_task())

        done, pending = await asyncio.wait(
            [listener_task, font_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

    except (WebSocketDisconnect, RuntimeError):
        logger.debug(f"WebSocket client disconnected for run {run_id}")
    except Exception as exc:
        logger.warning(f"Error in WebSocket subscription for run {run_id}: {exc}")
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            pass
