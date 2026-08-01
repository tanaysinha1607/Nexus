"""Unit tests for Phase 1.0 infrastructure features: attempt scoping, lease heartbeat, and runtime timeout."""

import asyncio
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from app.models import Artifact, Node, NodeStatus, NodeType, Run, RunStatus
from orchestrator import (
    HandlerConfig,
    HeartbeatConfig,
    RunScheduler,
    SchedulerConfig,
    resolve_node_readiness,
)
from orchestrator.handlers import HandlerResult
from sqlalchemy.exc import DBAPIError, IntegrityError


# ---------------------------------------------------------------------------
# Test 0A: Duplicate seed artifact raises IntegrityError
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_seed_artifact_raises_integrity_error(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed1 = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Prompt 1",
        )
        db.add(seed1)
        await db.commit()

        # Second seed artifact with same (run_id, kind, filename) and node_id=NULL
        seed2 = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Prompt 2",
        )
        db.add(seed2)
        with pytest.raises(IntegrityError):
            await db.commit()


# ---------------------------------------------------------------------------
# Test 0B: Seed artifact immutability trigger fires on UPDATE
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_seed_artifact_immutability_trigger(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Initial Content",
        )
        db.add(seed)
        await db.commit()

        # Attempt to UPDATE seed artifact content
        seed.content = "Modified Content"
        with pytest.raises((IntegrityError, DBAPIError)):
            await db.commit()


# ---------------------------------------------------------------------------
# Test 1: Selector scoping is no-op when all artifacts are attempt=1
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_selector_attempt_scoping_no_op(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Attempt1Node",
            node_type=NodeType.agent,
            attempt=1,
            config={"required_inputs": [{"kind": "kind_a"}]},
        )
        db.add(node)
        await db.flush()

        art = Artifact(
            project_id=test_project.id,
            node_id=node.id,
            run_id=run.id,
            filename="kind_a.txt",
            kind="kind_a",
            attempt=1,
            version=1,
            content="data",
        )
        db.add(art)
        await db.commit()

        is_ready, resolved = await resolve_node_readiness(db, node, run.id)
        assert is_ready
        assert resolved["kind_a"].id == art.id


# ---------------------------------------------------------------------------
# Test 2: Attempt scoping prevents cross-attempt bleed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_attempt_scoping_prevents_cross_attempt_bleed(session_factory, test_project):
    """An attempt-1 node must NOT resolve to an attempt-2 artifact even if attempt-2 artifact has a higher version."""
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_attempt_1 = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Attempt1Node",
            node_type=NodeType.agent,
            attempt=1,
            config={"required_inputs": [{"kind": "spec_kind"}]},
        )
        db.add(node_attempt_1)
        await db.flush()

        # Attempt 1 artifact (v1)
        art_att_1 = Artifact(
            project_id=test_project.id,
            node_id=node_attempt_1.id,
            run_id=run.id,
            filename="spec_att1.txt",
            kind="spec_kind",
            attempt=1,
            version=1,
            content="attempt 1 data",
        )
        # Attempt 2 artifact (v2 - higher version, created later)
        art_att_2 = Artifact(
            project_id=test_project.id,
            node_id=node_attempt_1.id,
            run_id=run.id,
            filename="spec_att2.txt",
            kind="spec_kind",
            attempt=2,
            version=2,
            content="attempt 2 data",
        )
        db.add_all([art_att_1, art_att_2])
        await db.commit()

        # Attempt-1 node should resolve to attempt-1 artifact, NOT attempt-2
        is_ready, resolved = await resolve_node_readiness(db, node_attempt_1, run.id)
        assert is_ready
        assert resolved["spec_kind"].id == art_att_1.id
        assert resolved["spec_kind"].attempt == 1


# ---------------------------------------------------------------------------
# Test 3: Lease heartbeat prevents node reclaim during long execution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lease_heartbeat_prevents_reclaim(session_factory, test_project, monkeypatch):
    """Handler running longer than lease_seconds is NOT reclaimed due to active lease heartbeat."""
    async def slow_agent_handler(node, inputs, config):
        await asyncio.sleep(0.6)  # Handler takes 0.6s
        return HandlerResult(status=NodeStatus.completed)

    monkeypatch.setattr(
        "orchestrator.scheduler.handle_agent_node", slow_agent_handler
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="SlowNode",
            node_type=NodeType.agent,
            config={"output_kind": "slow_out", "required_inputs": []},
        )
        db.add(node)
        await db.commit()
        run_id = run.id

    # Fast lease (0.2s) with heartbeat interval (0.05s)
    scheduler_cfg = SchedulerConfig(
        lease_seconds=0.2,
        poll_interval=0.05,
        heartbeat=HeartbeatConfig(interval_seconds=0.05, lease_seconds=0.2),
    )
    handler_cfg = HandlerConfig()

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=scheduler_cfg,
        handler_config=handler_cfg,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        n = res.scalar_one()
        assert n.status == NodeStatus.completed


# ---------------------------------------------------------------------------
# Test 4: Hung handler past max_node_runtime_seconds is cancelled and marked failed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hung_handler_runtime_timeout(session_factory, test_project, monkeypatch):
    """Handler hanging past max_node_runtime_seconds is cancelled and marked failed."""
    async def hung_agent_handler(node, inputs, config):
        await asyncio.sleep(10.0)  # Hung handler
        return HandlerResult(status=NodeStatus.completed)

    monkeypatch.setattr(
        "orchestrator.scheduler.handle_agent_node", hung_agent_handler
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="HungNode",
            node_type=NodeType.agent,
            config={"output_kind": "hung_out", "required_inputs": []},
        )
        db.add(node)
        await db.commit()
        run_id = run.id

    # Short max_node_runtime_seconds (0.2s)
    scheduler_cfg = SchedulerConfig(
        lease_seconds=1.0,
        poll_interval=0.05,
        max_node_runtime_seconds=0.2,
        heartbeat=HeartbeatConfig(interval_seconds=0.05, lease_seconds=1.0),
    )
    handler_cfg = HandlerConfig()

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=scheduler_cfg,
        handler_config=handler_cfg,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.failed

    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        n = res.scalar_one()
        assert n.status == NodeStatus.failed
        assert "timeout" in n.logs.lower()
