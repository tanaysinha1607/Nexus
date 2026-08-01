"""Event Bus interface and implementations for Nexus.

DESIGN & FAILURE POLICY CONTRACT:
  Postgres (runs.seq_counter) is the single source of truth for sequence counter
  allocation and run state. Redis Pub/Sub is a lightweight event transport.

  FAILURE POLICY:
  If a Redis publish fails (e.g. connection timeout or network hiccup), the DB
  transaction is already committed and is the authoritative state.
  The event bus logs the failure loudly with logger.error(), but MUST NOT roll back
  the database transaction and MUST NOT crash the run.
  WebSocket clients will automatically recover lost events via GET /api/runs/{id}/snapshot.
"""

import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class BaseEventBus(ABC):
    """Abstract interface for event transport."""

    @abstractmethod
    async def publish(self, run_id: uuid.UUID, event: dict[str, Any]) -> None:
        """Publish a JSON event dictionary to the run's channel."""
        pass


class RedisEventBus(BaseEventBus):
    """Redis Pub/Sub implementation of BaseEventBus."""

    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    async def publish(self, run_id: uuid.UUID, event: dict[str, Any]) -> None:
        channel = f"nexus:events:run:{run_id}"
        payload = json.dumps(event)
        try:
            await self.redis.publish(channel, payload)
        except Exception as exc:
            # FAILURE POLICY: Log loudly, do NOT raise, do NOT roll back DB
            logger.error(
                f"REDIS PUBLISH FAILURE on channel {channel}: {exc}. "
                f"DB transaction remains committed; clients can recover via snapshot.",
                exc_info=True,
            )


class MockEventBus(BaseEventBus):
    """In-memory event collector for unit testing without Redis dependency."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, run_id: uuid.UUID, event: dict[str, Any]) -> None:
        self.events.append(event)
