"""Unit tests for Phase 1.4a Rework Policy, Attempt Scoping, and Cap logic."""

import json
import uuid
import pytest
from pathlib import Path
from sqlalchemy import select

from app.models import Artifact, Node, NodeDependency, NodeStatus, NodeType, Run
from orchestrator.policy import after_node_completed
from orchestrator.readiness import resolve_node_readiness
from orchestrator.transitions import EventBuffer


@pytest.mark.asyncio
async def test_rework_loop_attempt_2_creation(session_factory, test_project):
    """Attempt 1 validator fails -> policy creates attempt 2 sub-chain & failure_context artifact."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        b1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Backend",
            node_type=NodeType.agent, agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]}, attempt=1, status=NodeStatus.completed,
        )
        e1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="BackendExecutor",
            node_type=NodeType.executor, agent_role="backend_executor",
            config={"required_inputs": [{"kind": "source_code"}]}, attempt=1, status=NodeStatus.completed,
        )
        v1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Validator",
            node_type=NodeType.validator, agent_role="validator",
            config={"required_inputs": [{"kind": "execution_report"}]}, attempt=1, status=NodeStatus.completed,
        )

        rep_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=e1_node.id, run_id=run_id,
            filename="execution_report.json", kind="execution_report", produced_by_role="backend_executor",
            content=json.dumps({
                "build_success": True, "container_started": True, "health_ok": False,
                "health_status_code": 500, "container_logs_tail": "ModuleNotFoundError: No module named 'email-validator'",
            }), version=1, attempt=1
        )
        verdict_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=v1_node.id, run_id=run_id,
            filename="verdict.json", kind="verdict", produced_by_role="validator",
            content=json.dumps({"passed": False, "failures": ["health_check_failed"]}), version=1, attempt=1
        )

        db.add_all([b1_node, e1_node, v1_node, rep_art, verdict_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, v1_node, event_buffer, run_id, max_attempts=3)
        await db.commit()

        assert len(new_nodes) == 4
        b2_node, e2_node, v2_node = new_nodes[0], new_nodes[1], new_nodes[2]

        assert b2_node.name == "Backend_a2"
        assert b2_node.attempt == 2
        assert b2_node.rework_of_id == b1_node.id
        assert e2_node.attempt == 2
        assert v2_node.attempt == 2

        # Check failure_context artifact created
        fail_art = (await db.execute(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "failure_context")
        )).scalar_one_or_none()

        assert fail_art is not None
        assert fail_art.attempt == 2
        content = json.loads(fail_art.content)
        assert content["attempt"] == 1
        assert "ModuleNotFoundError: No module named 'email-validator'" in content["container_logs_tail"]


@pytest.mark.asyncio
async def test_rework_loop_cap_3_attempts_max(session_factory, test_project):
    """Attempt 3 fails -> max attempts cap reached -> no 4th attempt created."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        b3_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Backend_a3",
            node_type=NodeType.agent, agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]}, attempt=3, status=NodeStatus.completed,
        )
        v3_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Validator_a3",
            node_type=NodeType.validator, agent_role="validator",
            config={"required_inputs": [{"kind": "execution_report"}]}, attempt=3, status=NodeStatus.completed,
        )
        verdict_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=v3_node.id, run_id=run_id,
            filename="verdict.json", kind="verdict", produced_by_role="validator",
            content=json.dumps({"passed": False, "failures": ["health_check_failed"]}), version=1, attempt=3
        )
        db.add_all([b3_node, v3_node, verdict_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, v3_node, event_buffer, run_id, max_attempts=3)

        assert new_nodes == []


@pytest.mark.asyncio
async def test_pass_first_try_no_rework_subchain(session_factory, test_project):
    """Attempt 1 passes -> policy creates no rework sub-chain."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        v1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Validator",
            node_type=NodeType.validator, agent_role="validator", attempt=1, status=NodeStatus.completed,
        )
        verdict_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=v1_node.id, run_id=run_id,
            filename="verdict.json", kind="verdict", produced_by_role="validator",
            content=json.dumps({"passed": True, "failures": []}), version=1, attempt=1
        )
        db.add_all([v1_node, verdict_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, v1_node, event_buffer, run_id, max_attempts=3)

        assert new_nodes == []


@pytest.mark.asyncio
async def test_attempt_scoping_resolves_attempt_1_api_contract(session_factory, test_project):
    """Backend#2 (attempt 2) successfully resolves attempt-1 api_contract and attempt-2 failure_context."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        contract_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id,
            filename="api_contract.json", kind="api_contract", produced_by_role="api_designer",
            content='{"endpoints": []}', version=1, attempt=1
        )
        fail_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id,
            filename="failure_context.json", kind="failure_context", produced_by_role="validator",
            content='{"error": "bad pin"}', version=1, attempt=2
        )
        src_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id,
            filename="main.py", kind="source_code", produced_by_role="backend_engineer",
            content="print('app')", version=1, attempt=1
        )

        b2_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Backend_a2",
            node_type=NodeType.agent, agent_role="backend_engineer", attempt=2,
            config={
                "required_inputs": [
                    {"kind": "api_contract"},
                    {"kind": "source_code"},
                    {"kind": "failure_context"},
                ]
            }
        )
        db.add_all([contract_art, fail_art, src_art, b2_node])
        await db.commit()

        is_ready, resolved = await resolve_node_readiness(db, b2_node, run_id)
        assert is_ready is True
        assert "api_contract" in resolved
        assert resolved["api_contract"].attempt == 1
        assert resolved["failure_context"].attempt == 2


def test_policy_boundary_scheduler_contains_no_verdict_semantics():
    """Confirms scheduler.py contains no verdict decision logic or 'passed' evaluation (policy encapsulation)."""
    scheduler_file = Path(__file__).resolve().parent.parent / "orchestrator" / "scheduler.py"
    content = scheduler_file.read_text(encoding="utf-8")
    assert '"passed"' not in content
    assert "'passed'" not in content
    assert "verdict_art" not in content
    assert "rework" not in content


@pytest.mark.asyncio
async def test_reviewer_approves_working_code_completes_run(session_factory, test_project):
    """Attempt 1 validator passes and Reviewer approves -> no rework sub-chain spawned."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        r1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Reviewer",
            node_type=NodeType.agent, agent_role="senior_reviewer", attempt=1, status=NodeStatus.completed,
        )
        rev_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=r1_node.id, run_id=run_id,
            filename="review.md", kind="review", produced_by_role="senior_reviewer",
            content="# Code Review\n- Security: clean\n\nREVIEW_VERDICT: approved", version=1, attempt=1
        )
        db.add_all([r1_node, rev_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, r1_node, event_buffer, run_id, max_attempts=5)
        assert new_nodes == []


@pytest.mark.asyncio
async def test_reviewer_changes_requested_spawns_attempt_2_with_review_feedback(session_factory, test_project):
    """Attempt 1 validator passes but Reviewer requests changes -> attempt 2 sub-chain spawned with review_feedback artifact."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        b1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Backend",
            node_type=NodeType.agent, agent_role="backend_engineer", attempt=1, status=NodeStatus.completed,
        )
        v1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Validator",
            node_type=NodeType.validator, agent_role="validator", attempt=1, status=NodeStatus.completed,
        )
        r1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Reviewer",
            node_type=NodeType.agent, agent_role="senior_reviewer", attempt=1, status=NodeStatus.completed,
        )
        rev_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=r1_node.id, run_id=run_id,
            filename="review.md", kind="review", produced_by_role="senior_reviewer",
            content="# Code Review\n- Security: Password hashing uses plain sha256; use bcrypt.\n\nREVIEW_VERDICT: changes_requested", version=1, attempt=1
        )
        db.add_all([b1_node, v1_node, r1_node, rev_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, r1_node, event_buffer, run_id, max_attempts=5)
        await db.commit()

        assert len(new_nodes) == 4
        b2_node = new_nodes[0]
        assert b2_node.name == "Backend_a2"
        assert b2_node.attempt == 2

        # Check review_feedback artifact (NOT failure_context) created
        fb_art = (await db.execute(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "review_feedback")
        )).scalar_one_or_none()

        assert fb_art is not None
        assert fb_art.attempt == 2
        content = json.loads(fb_art.content)
        assert content["reviewer_verdict"] == "changes_requested"
        assert "plain sha256" in content["review_comments"]


@pytest.mark.asyncio
async def test_validator_fail_cancels_reviewer_and_spawns_rework(session_factory, test_project):
    """Validator FAIL cancels pending Reviewer node for that attempt so stuck-detection ignores it."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        b1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Backend",
            node_type=NodeType.agent, agent_role="backend_engineer", attempt=1, status=NodeStatus.completed,
        )
        v1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Validator",
            node_type=NodeType.validator, agent_role="validator", attempt=1, status=NodeStatus.completed,
        )
        r1_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Reviewer",
            node_type=NodeType.agent, agent_role="senior_reviewer", attempt=1, status=NodeStatus.pending,
        )
        verdict_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=v1_node.id, run_id=run_id,
            filename="verdict.json", kind="verdict", produced_by_role="validator",
            content=json.dumps({"passed": False, "failures": ["health_check_failed"]}), version=1, attempt=1
        )
        db.add_all([b1_node, v1_node, r1_node, verdict_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, v1_node, event_buffer, run_id, max_attempts=5)
        await db.commit()

        # Reviewer_a1 MUST be transitioned to cancelled
        await db.refresh(r1_node)
        assert r1_node.status == NodeStatus.cancelled
        assert len(new_nodes) == 4


@pytest.mark.asyncio
async def test_reviewer_changes_requested_stops_at_max_attempts(session_factory, test_project):
    """When attempt count reaches max_attempts (5), Senior Reviewer changes_requested stops rework and spawns NO new sub-chain."""
    run_id = uuid.uuid4()
    async with session_factory() as db:
        run = Run(id=run_id, project_id=test_project.id)
        db.add(run)

        # Create 5 backend engineer nodes (attempts 1 to 5)
        backend_nodes = []
        for att in range(1, 6):
            b_node = Node(
                id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name=f"Backend_a{att}" if att > 1 else "Backend",
                node_type=NodeType.agent, agent_role="backend_engineer", attempt=att, status=NodeStatus.completed,
            )
            backend_nodes.append(b_node)

        r5_node = Node(
            id=uuid.uuid4(), project_id=test_project.id, run_id=run_id, name="Reviewer_a5",
            node_type=NodeType.agent, agent_role="senior_reviewer", attempt=5, status=NodeStatus.completed,
        )
        rev_art = Artifact(
            id=uuid.uuid4(), project_id=test_project.id, node_id=r5_node.id, run_id=run_id,
            filename="review.md", kind="review", produced_by_role="senior_reviewer",
            content="# Code Review\n- Security: Password hashing issue remains.\n\nREVIEW_VERDICT: changes_requested", version=1, attempt=5
        )
        db.add_all(backend_nodes + [r5_node, rev_art])
        await db.commit()

        event_buffer = EventBuffer()
        new_nodes = await after_node_completed(db, r5_node, event_buffer, run_id, max_attempts=5)
        assert new_nodes == []


