"""Comprehensive pytest suite for the Nexus orchestrator (14 tests).

Includes real Redis event bus testing, artifact event emission, and WebSocket reconnect contract verification.
"""

import asyncio
import json
import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from redis.asyncio import from_url
from sqlalchemy import select

from app.event_bus import RedisEventBus
from app.main import app
from app.models import (
    Artifact,
    Node,
    NodeDependency,
    NodeStatus,
    NodeType,
    Project,
    Run,
    RunStatus,
)
from orchestrator import (
    EventBuffer,
    HandlerConfig,
    RunScheduler,
    SchedulerConfig,
    claim_node,
    detect_dag_cycle,
    flush_events,
    resolve_node_readiness,
    transition,
)
from orchestrator.handlers.agent import handle_agent_node
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.transitions import buffer_artifact_created, buffer_run_status_changed

TEST_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Fast timing configuration for testing
TEST_SCHEDULER_CONFIG = SchedulerConfig(lease_seconds=0.1, poll_interval=0.05, use_real_agents=False)
TEST_HANDLER_CONFIG = HandlerConfig(agent_sleep_range=(0.0, 0.0), executor_sleep_range=(0.0, 0.0))


# ---------------------------------------------------------------------------
# Test 1: Linear A -> B -> C executes in dependency order
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_linear_dag_execution_order(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="NodeA",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="NodeB",
            node_type=NodeType.agent,
            config={"output_kind": "kind_b", "required_inputs": [{"kind": "kind_a"}]},
        )
        node_c = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="NodeC",
            node_type=NodeType.agent,
            config={"output_kind": "kind_c", "required_inputs": [{"kind": "kind_b"}]},
        )
        db.add_all([node_a, node_b, node_c])
        await db.flush()

        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        db.add(NodeDependency(node_id=node_c.id, depends_on_node_id=node_b.id))
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
    )
    final_status = await scheduler.run()

    assert final_status == RunStatus.completed
    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        nodes = {n.name: n for n in res.scalars().all()}
        assert nodes["NodeA"].status == NodeStatus.completed
        assert nodes["NodeB"].status == NodeStatus.completed
        assert nodes["NodeC"].status == NodeStatus.completed


# ---------------------------------------------------------------------------
# Test 2: Diamond A -> (B, C) -> D HARDENED Parallelism (Barrier Synchronization)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_diamond_dag_parallelism(session_factory, test_project, monkeypatch):
    barrier = asyncio.Barrier(2)
    original_handle_agent = handle_agent_node

    async def barrier_handle_agent(node, inputs, config):
        if node.name in ("B", "C"):
            await asyncio.wait_for(barrier.wait(), timeout=2.0)
        return await original_handle_agent(node, inputs, config)

    monkeypatch.setattr(
        "orchestrator.scheduler.handle_agent_node", barrier_handle_agent
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="A",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="B",
            node_type=NodeType.agent,
            config={"output_kind": "kind_b", "required_inputs": [{"kind": "kind_a"}]},
        )
        node_c = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="C",
            node_type=NodeType.agent,
            config={"output_kind": "kind_c", "required_inputs": [{"kind": "kind_a"}]},
        )
        node_d = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="D",
            node_type=NodeType.agent,
            config={
                "output_kind": "kind_d",
                "required_inputs": [{"kind": "kind_b"}, {"kind": "kind_c"}],
            },
        )
        db.add_all([node_a, node_b, node_c, node_d])
        await db.flush()

        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        db.add(NodeDependency(node_id=node_c.id, depends_on_node_id=node_a.id))
        db.add(NodeDependency(node_id=node_d.id, depends_on_node_id=node_b.id))
        db.add(NodeDependency(node_id=node_d.id, depends_on_node_id=node_c.id))
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
    )
    final_status = await scheduler.run()

    assert final_status == RunStatus.completed
    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        nodes = {n.name: n for n in res.scalars().all()}
        assert all(n.status == NodeStatus.completed for n in nodes.values())


# ---------------------------------------------------------------------------
# Test 3: Failed node blocks all transitive descendants
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_node_blocks_descendants(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a_id = uuid.uuid4()
        node_a = Node(
            id=node_a_id,
            project_id=test_project.id,
            run_id=run.id,
            name="FailA",
            node_type=NodeType.validator,
            config={"required_inputs": [{"kind": "bad_stdout"}]},
        )
        bad_art = Artifact(
            project_id=test_project.id,
            node_id=node_a_id,
            run_id=run.id,
            filename="bad_stdout.json",
            kind="bad_stdout",
            content="NOT VALID JSON",
        )
        db.add(bad_art)
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="DescB",
            node_type=NodeType.agent,
            config={"required_inputs": [{"kind": "whatever"}]},
        )

        node_x = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="IndependentX",
            node_type=NodeType.agent,
            config={"output_kind": "kind_x", "required_inputs": []},
        )
        node_y = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="IndependentY",
            node_type=NodeType.agent,
            config={"output_kind": "kind_y", "required_inputs": [{"kind": "kind_x"}]},
        )
        db.add_all([node_a, node_b, node_x, node_y])
        await db.flush()

        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        db.add(NodeDependency(node_id=node_y.id, depends_on_node_id=node_x.id))
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
    )
    final_status = await scheduler.run()

    assert final_status == RunStatus.failed
    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        nodes = {n.name: n for n in res.scalars().all()}
        assert nodes["FailA"].status == NodeStatus.failed
        assert nodes["DescB"].status == NodeStatus.blocked
        assert nodes["IndependentX"].status == NodeStatus.completed
        assert nodes["IndependentY"].status == NodeStatus.completed


# ---------------------------------------------------------------------------
# Test 4: Cyclic graph rejected at creation
# ---------------------------------------------------------------------------
def test_cyclic_dag_rejection():
    node_ids = ["A", "B", "C"]
    edges = [("B", "A"), ("C", "B"), ("A", "C")]

    is_valid, cycle_members = detect_dag_cycle(node_ids, edges)
    assert not is_valid
    assert set(cycle_members) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Test 5: Node with unsatisfiable selector: run terminates as STUCK/failed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stuck_node_detection(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="FinishedA",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="StuckB",
            node_type=NodeType.agent,
            config={
                "output_kind": "kind_b",
                "required_inputs": [
                    {"kind": "impossible_kind", "from_role": "nonexistent_role"}
                ],
            },
        )
        db.add_all([node_a, node_b])
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
    )

    final_status = await asyncio.wait_for(scheduler.run(), timeout=3.0)

    assert final_status == RunStatus.failed
    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        nodes = {n.name: n for n in res.scalars().all()}
        assert nodes["FinishedA"].status == NodeStatus.completed
        assert nodes["StuckB"].status == NodeStatus.blocked
        assert "STUCK" in nodes["StuckB"].logs


# ---------------------------------------------------------------------------
# Test 6: Node stranded in 'running' with expired lease is reclaimed & re-run
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lease_expiration_reclaim(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="StrandedNode",
            node_type=NodeType.agent,
            status=NodeStatus.running,
            claimed_by="dead-worker",
            lease_expires_at=past_time,
            config={"output_kind": "kind_stranded", "required_inputs": []},
        )
        db.add(node)
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
    )
    final_status = await scheduler.run()

    assert final_status == RunStatus.completed
    async with session_factory() as db:
        res = await db.execute(select(Node).where(Node.run_id == run_id))
        n = res.scalar_one()
        assert n.status == NodeStatus.completed


# ---------------------------------------------------------------------------
# Test 7: Validator determinism
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validator_determinism():
    stdout_art = Artifact(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        filename="stdout.json",
        kind="stdout",
        content=json.dumps({"exit_code": 0, "stdout": "ok"}),
    )
    inputs = {"stdout": stdout_art}
    val_node = Node(name="Validator", node_type=NodeType.validator)
    config = HandlerConfig()

    verdicts = []
    for _ in range(100):
        res = await handle_validator_node(val_node, inputs, config)
        verdicts.append(res.artifacts[0].content)

    assert len(set(verdicts)) == 1
    assert verdicts[0] == "pass"


# ---------------------------------------------------------------------------
# Test 8: WS sequence monotonicity (Mock Event Bus)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ws_sequence_monotonicity(session_factory, test_project):
    class MockBus:
        def __init__(self):
            self.events = []

        async def publish(self, run_id, event):
            self.events.append(event)

    event_bus = MockBus()

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="SeqA",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="SeqB",
            node_type=NodeType.agent,
            config={"output_kind": "kind_b", "required_inputs": [{"kind": "kind_a"}]},
        )
        db.add_all([node_a, node_b])
        await db.flush()
        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        await db.commit()

        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=TEST_SCHEDULER_CONFIG,
        handler_config=TEST_HANDLER_CONFIG,
        event_bus=event_bus,
    )
    await scheduler.run()

    seqs = [evt["seq"] for evt in event_bus.events]
    assert len(seqs) > 0
    assert seqs == list(range(1, len(seqs) + 1))


# ---------------------------------------------------------------------------
# Test 9: Readiness ignores parent status
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_readiness_ignores_parent_status(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        parent = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ParentStillRunning",
            node_type=NodeType.agent,
            status=NodeStatus.running,
        )
        child = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ChildNode",
            node_type=NodeType.agent,
            status=NodeStatus.pending,
            config={"required_inputs": [{"kind": "early_artifact"}]},
        )
        db.add_all([parent, child])
        await db.flush()

        art = Artifact(
            project_id=test_project.id,
            node_id=parent.id,
            run_id=run.id,
            filename="early.txt",
            kind="early_artifact",
            content="data",
        )
        db.add(art)
        await db.commit()

        is_ready, resolved = await resolve_node_readiness(db, child, run.id)
        assert is_ready
        assert "early_artifact" in resolved


# ---------------------------------------------------------------------------
# Test 10: Two concurrent claim attempts on same ready node (exactly 1 winner)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrency_race_single_winner(session_factory, test_project):
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="RaceNode",
            node_type=NodeType.agent,
            status=NodeStatus.ready,
            config={"required_inputs": []},
        )
        db.add(node)
        await db.commit()

        run_id = run.id

    async def claim_task(worker_name: str):
        async with session_factory() as session:
            return await claim_node(session, run_id, worker_name, lease_seconds=60.0)

    res1, res2 = await asyncio.gather(claim_task("worker-1"), claim_task("worker-2"))
    winners = [r for r in (res1, res2) if r is not None]
    assert len(winners) == 1


# ---------------------------------------------------------------------------
# Test 11: Validator exit_code=0 -> pass, exit_code=1 -> fail, node status 'completed' in BOTH
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validator_exit_code_handling(session_factory, test_project):
    async def run_with_exit_code(exit_code: int):
        async with session_factory() as db:
            run = Run(project_id=test_project.id)
            db.add(run)
            await db.flush()

            executor = Node(
                project_id=test_project.id,
                run_id=run.id,
                name="ExecNode",
                node_type=NodeType.executor,
                agent_role="exec_role",
                config={"exit_code": exit_code, "required_inputs": []},
            )
            validator = Node(
                project_id=test_project.id,
                run_id=run.id,
                name="ValNode",
                node_type=NodeType.validator,
                agent_role="val_role",
                config={"required_inputs": [{"kind": "stdout", "from_role": "exec_role"}]},
            )
            db.add_all([executor, validator])
            await db.flush()

            db.add(NodeDependency(node_id=validator.id, depends_on_node_id=executor.id))
            await db.commit()

            run_id = run.id

        scheduler = RunScheduler(
            session_factory=session_factory,
            run_id=run_id,
            scheduler_config=TEST_SCHEDULER_CONFIG,
            handler_config=TEST_HANDLER_CONFIG,
        )
        await scheduler.run()

        async with session_factory() as db:
            v_node = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "ValNode"))).scalar_one()
            verdict_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "verdict"))).scalar_one()
            return v_node.status, verdict_art.content

    val_status_0, verdict_0 = await run_with_exit_code(0)
    val_status_1, verdict_1 = await run_with_exit_code(1)

    assert val_status_0 == NodeStatus.completed
    assert verdict_0 == "pass"

    assert val_status_1 == NodeStatus.completed
    assert verdict_1 == "fail"


# ---------------------------------------------------------------------------
# Test 12: Executor stub emits parseable JSON
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_executor_json_parsing():
    valid_art = Artifact(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        filename="stdout.json",
        kind="stdout",
        content=json.dumps({"exit_code": 0, "stdout": "worked"}),
    )
    val_node = Node(name="Val", node_type=NodeType.validator)
    cfg = HandlerConfig()

    res = await handle_validator_node(val_node, {"stdout": valid_art}, cfg)
    assert res.status == NodeStatus.completed
    assert res.artifacts[0].content == "pass"

    invalid_art = Artifact(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        node_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        filename="stdout.json",
        kind="stdout",
        content="NOT VALID JSON",
    )
    res_bad = await handle_validator_node(val_node, {"stdout": invalid_art}, cfg)
    assert res_bad.status == NodeStatus.failed


# ---------------------------------------------------------------------------
# Test 13: REAL Redis Event Bus & WebSocket Client Sequence Monotonicity
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_real_websocket_sequence_monotonicity(session_factory, test_project):
    """Subscribes real WebSocket to /ws/runs/{id} using real Redis Pub/Sub."""
    redis_client = from_url(TEST_REDIS_URL, decode_responses=True)
    redis_event_bus = RedisEventBus(redis_client)

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="WsNodeA",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="WsNodeB",
            node_type=NodeType.agent,
            config={"output_kind": "kind_b", "required_inputs": [{"kind": "kind_a"}]},
        )
        db.add_all([node_a, node_b])
        await db.flush()
        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        await db.commit()
        run_id = run.id

    received_events = []

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
            scheduler = RunScheduler(
                session_factory=session_factory,
                run_id=run_id,
                scheduler_config=TEST_SCHEDULER_CONFIG,
                handler_config=TEST_HANDLER_CONFIG,
                event_bus=redis_event_bus,
            )
            await scheduler.run()

            # Events emitted: run_status_changed (running), node_status (ready->running), artifact_created, node_status (running->completed), ...
            for _ in range(7):
                data = websocket.receive_json()
                received_events.append(data)

    await redis_client.aclose()

    seqs = [evt["seq"] for evt in received_events]
    assert len(seqs) >= 5
    assert seqs == list(range(1, len(seqs) + 1))


# ---------------------------------------------------------------------------
# Test 14: WebSocket Reconnect Contract (Nodes, Artifacts & Run Status)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_websocket_reconnect_contract(session_factory, test_project):
    """Verifies: disconnect mid-run -> GET snapshot -> reconnect -> filter seq > snapshot.seq -> complete state (nodes AND artifacts)."""
    redis_client = from_url(TEST_REDIS_URL, decode_responses=True)
    redis_event_bus = RedisEventBus(redis_client)

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        node_a = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ReconA",
            node_type=NodeType.agent,
            config={"output_kind": "kind_a", "required_inputs": []},
        )
        node_b = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ReconB",
            node_type=NodeType.agent,
            config={"output_kind": "kind_b", "required_inputs": [{"kind": "kind_a"}]},
        )
        db.add_all([node_a, node_b])
        await db.flush()
        db.add(NodeDependency(node_id=node_b.id, depends_on_node_id=node_a.id))
        await db.commit()
        run_id = run.id

    with TestClient(app) as client:
        # Phase 1: Connect WS 1 and emit NodeA events
        ws1_events = []
        with client.websocket_connect(f"/ws/runs/{run_id}") as ws1:
            await asyncio.sleep(0.1)
            async with session_factory() as db:
                node_a_obj = await db.get(Node, node_a.id)
                event_buffer = EventBuffer()
                await transition(db, node_a_obj, NodeStatus.running, "claimed", event_buffer)
                art_id = uuid.uuid4()
                art_a = Artifact(
                    id=art_id,
                    project_id=test_project.id,
                    node_id=node_a.id,
                    run_id=run_id,
                    filename="kind_a.md",
                    kind="kind_a",
                    produced_by_role="agent",
                    content="a",
                )
                db.add(art_a)
                buffer_artifact_created(event_buffer, run_id, node_a.id, "agent", art_id, "kind_a.md", "kind_a", "agent", 1)
                await transition(db, node_a_obj, NodeStatus.completed, "finished", event_buffer)
                await flush_events(db, run_id, event_buffer, redis_event_bus)

            # Receive 3 events (running, artifact_created, completed)
            for _ in range(3):
                ws1_events.append(ws1.receive_json())

        # Phase 2: WS 1 disconnected. Fetch Snapshot via GET /api/runs/{id}/snapshot
        res = client.get(f"/api/runs/{run_id}/snapshot")
        assert res.status_code == 200
        snapshot_data = res.json()
        snapshot_seq = snapshot_data["seq_counter"]

        # Phase 3: Reconnect WS 2 and emit NodeB events
        ws2_events = []
        with client.websocket_connect(f"/ws/runs/{run_id}") as ws2:
            await asyncio.sleep(0.1)
            async with session_factory() as db:
                node_b_obj = await db.get(Node, node_b.id)
                event_buffer = EventBuffer()
                await transition(db, node_b_obj, NodeStatus.running, "claimed", event_buffer)
                art_id_b = uuid.uuid4()
                art_b = Artifact(
                    id=art_id_b,
                    project_id=test_project.id,
                    node_id=node_b.id,
                    run_id=run_id,
                    filename="kind_b.md",
                    kind="kind_b",
                    produced_by_role="agent",
                    content="b",
                )
                db.add(art_b)
                buffer_artifact_created(event_buffer, run_id, node_b.id, "agent", art_id_b, "kind_b.md", "kind_b", "agent", 1)
                await transition(db, node_b_obj, NodeStatus.completed, "finished", event_buffer)
                buffer_run_status_changed(event_buffer, run_id, "running", "completed", "Run finished")
                await flush_events(db, run_id, event_buffer, redis_event_bus)

            # Receive 4 events
            for _ in range(4):
                ws2_events.append(ws2.receive_json())

        # Reconnect Contract: Discard events with seq <= snapshot.seq
        filtered_post_reconnect = [e for e in ws2_events if e["seq"] > snapshot_seq]

        # Reconstruct nodes and artifacts state from snapshot + filtered events
        reconstructed_nodes = {n["name"]: n["status"] for n in snapshot_data["nodes"]}
        reconstructed_artifact_kinds = {a["kind"] for a in snapshot_data["artifacts"]}

        for evt in filtered_post_reconnect:
            if evt["type"] == "node_status_changed":
                for n in snapshot_data["nodes"]:
                    if n["id"] == evt["node_id"]:
                        reconstructed_nodes[n["name"]] = evt["new_status"]
            elif evt["type"] == "artifact_created":
                reconstructed_artifact_kinds.add(evt["kind"])

        # Assert reconstructed state covers both nodes AND artifacts
        assert reconstructed_nodes["ReconA"] == "completed"
        assert reconstructed_nodes["ReconB"] == "completed"
        assert "kind_a" in reconstructed_artifact_kinds
        assert "kind_b" in reconstructed_artifact_kinds

        # Verify gapless sequence numbers across reconnect
        all_seqs = [e["seq"] for e in ws1_events] + [snapshot_seq] + [e["seq"] for e in filtered_post_reconnect]
        assert all_seqs == [1, 2, 3, 3, 4, 5, 6, 7]

    await redis_client.aclose()
