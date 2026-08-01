"""State transition engine and event publisher.

DESIGN INVARIANT:
  Only transition() and claim_node() are permitted to update nodes.status or
  emit transition events. No other code in the repository may write nodes.status.

EVENT EMISSION POST-COMMIT CONTRACT:
  Transition events are buffered during DB operations. Sequence numbers (seq)
  are allocated atomically at flush time AFTER the DB transaction commits.
  On transaction rollback, buffered events are discarded, guaranteeing gapless
  sequence numbering across all committed transition events.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node, NodeStatus, NodeType

logger = logging.getLogger(__name__)


@dataclass
class EventEnvelope:
    """Standard envelope for all Nexus events."""

    type: str  # 'node_status_changed' | 'artifact_created' | 'run_status_changed'
    run_id: uuid.UUID
    node_id: uuid.UUID | None
    node_type: str | None
    ts: str
    seq: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "type": self.type,
            "run_id": str(self.run_id),
            "seq": self.seq,
            "ts": self.ts,
            "node_id": str(self.node_id) if self.node_id else None,
            "node_type": self.node_type if self.node_type else None,
        }
        data.update(self.payload)
        return data


class EventBuffer:
    """Buffer for uncommitted events attached to a session/context."""

    def __init__(self) -> None:
        self._pending_events: list[EventEnvelope] = []

    def add(self, event: EventEnvelope) -> None:
        self._pending_events.append(event)

    def clear(self) -> None:
        self._pending_events.clear()

    @property
    def pending_events(self) -> list[EventEnvelope]:
        return list(self._pending_events)


async def transition(
    session: AsyncSession,
    node: Node,
    to_state: NodeStatus,
    reason: str,
    event_buffer: EventBuffer | None = None,
) -> None:
    """Transition a node's status and buffer a node_status_changed event.

    Args:
        session: Active AsyncSession.
        node: The node instance to transition.
        to_state: Target NodeStatus.
        reason: Explanation for transition.
        event_buffer: Optional EventBuffer to hold uncommitted events.
    """
    from_state = node.status
    node.status = to_state
    node.updated_at = datetime.now(timezone.utc)

    event = EventEnvelope(
        type="node_status_changed",
        run_id=node.run_id,
        node_id=node.id,
        node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        ts=datetime.now(timezone.utc).isoformat(),
        payload={
            "old_status": from_state.value if hasattr(from_state, "value") else str(from_state),
            "new_status": to_state.value if hasattr(to_state, "value") else str(to_state),
            "reason": reason,
        },
    )

    if event_buffer is not None:
        event_buffer.add(event)


def buffer_run_status_changed(
    event_buffer: EventBuffer,
    run_id: uuid.UUID,
    old_status: str,
    new_status: str,
    reason: str,
) -> None:
    """Buffer a run_status_changed event."""
    event = EventEnvelope(
        type="run_status_changed",
        run_id=run_id,
        node_id=None,
        node_type=None,
        ts=datetime.now(timezone.utc).isoformat(),
        payload={
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        },
    )
    event_buffer.add(event)


def buffer_artifact_created(
    event_buffer: EventBuffer,
    run_id: uuid.UUID,
    node_id: uuid.UUID,
    node_type: str,
    artifact_id: uuid.UUID,
    filename: str,
    kind: str,
    produced_by_role: str | None,
    version: int,
) -> None:
    """Buffer an artifact_created event."""
    event = EventEnvelope(
        type="artifact_created",
        run_id=run_id,
        node_id=node_id,
        node_type=node_type,
        ts=datetime.now(timezone.utc).isoformat(),
        payload={
            "artifact_id": str(artifact_id),
            "filename": filename,
            "kind": kind,
            "produced_by_role": produced_by_role,
            "version": version,
        },
    )
    event_buffer.add(event)


def buffer_node_created(
    event_buffer: EventBuffer,
    run_id: uuid.UUID,
    node_id: uuid.UUID,
    name: str,
    node_type: str,
    status: str,
) -> None:
    """Buffer a node_created event."""
    event = EventEnvelope(
        type="node_created",
        run_id=run_id,
        node_id=node_id,
        node_type=node_type,
        ts=datetime.now(timezone.utc).isoformat(),
        payload={
            "name": name,
            "status": status,
        },
    )
    event_buffer.add(event)


async def flush_events(
    session: AsyncSession,
    run_id: uuid.UUID,
    event_buffer: EventBuffer,
    event_bus: Any | None = None,
) -> list[dict[str, Any]]:
    """Allocate sequence numbers post-commit and publish buffered events.

    Atomically increments runs.seq_counter for each event and publishes via event_bus.
    """
    flushed_dicts: list[dict[str, Any]] = []
    events = event_buffer.pending_events
    if not events:
        return flushed_dicts

    for evt in events:
        # Atomic sequence counter increment in Postgres (source of truth)
        stmt = text(
            "UPDATE runs SET seq_counter = seq_counter + 1 "
            "WHERE id = :run_id RETURNING seq_counter"
        )
        res = await session.execute(stmt, {"run_id": run_id})
        new_seq = res.scalar_one()
        evt.seq = new_seq

        evt_dict = evt.to_dict()
        flushed_dicts.append(evt_dict)

        if event_bus is not None:
            await event_bus.publish(run_id, evt_dict)

    await session.commit()
    event_buffer.clear()
    return flushed_dicts
