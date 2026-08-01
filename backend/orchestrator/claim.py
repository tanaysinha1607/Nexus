"""Standalone node claiming implementation using FOR UPDATE SKIP LOCKED.

DOCUMENTED EXCEPTION:
  claim_node() is the ONLY permitted exception to transition() for setting
  nodes.status, because node claiming must be atomic with row locking.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Node, NodeStatus
from orchestrator.readiness import resolve_node_readiness
from orchestrator.transitions import EventBuffer, EventEnvelope

logger = logging.getLogger(__name__)


async def claim_node(
    session: AsyncSession,
    run_id: uuid.UUID,
    worker_id: str,
    lease_seconds: float = 60.0,
    event_buffer: EventBuffer | None = None,
) -> Node | None:
    """Attempt to claim a single ready node in a run atomically using FOR UPDATE SKIP LOCKED.

    Args:
        session: Active AsyncSession.
        run_id: UUID of the execution run.
        worker_id: Identifier of the worker claiming the node.
        lease_seconds: Lease duration in seconds.
        event_buffer: Optional EventBuffer to collect transition events.

    Returns:
        Node | None: The claimed node if successful, else None.
    """
    stmt = (
        select(Node)
        .where(Node.run_id == run_id, Node.status == NodeStatus.ready)
        .order_by(Node.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await session.execute(stmt)
    node = result.scalar_one_or_none()

    if node is None:
        return None

    # Resolve input artifacts for provenance
    is_ready, resolved_inputs = await resolve_node_readiness(session, node, run_id)
    if not is_ready:
        return None

    # Update node config with resolved input artifact IDs
    config = dict(node.config or {})
    config["resolved_inputs"] = {k: str(v.id) for k, v in resolved_inputs.items()}
    node.config = config

    # Atomically transition state and set lease
    from_state = node.status
    node.status = NodeStatus.running
    node.claimed_by = worker_id
    node.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    node.updated_at = datetime.now(timezone.utc)

    # Buffer transition event if event_buffer provided
    if event_buffer is not None:
        event = EventEnvelope(
            type="node_status_changed",
            run_id=node.run_id,
            node_id=node.id,
            node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
            ts=datetime.now(timezone.utc).isoformat(),
            payload={
                "old_status": from_state.value if hasattr(from_state, "value") else str(from_state),
                "new_status": NodeStatus.running.value,
                "reason": f"Claimed by worker {worker_id}",
            },
        )
        event_buffer.add(event)

    await session.commit()
    return node
