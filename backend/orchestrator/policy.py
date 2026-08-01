"""Nexus Rework Policy Engine.

BOUNDARY RULE:
The scheduler calls policy generically ("a node finished, ask policy if anything follows").
Policy imports verdict semantics and handles rework sub-chain creation; the scheduler must not.
"""

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Node, NodeDependency, NodeStatus, NodeType
from orchestrator.transitions import EventBuffer, buffer_artifact_created, buffer_node_created, transition

logger = logging.getLogger(__name__)


async def after_node_completed(
    session: AsyncSession,
    completed_node: Node,
    event_buffer: EventBuffer,
    run_id: uuid.UUID,
    max_attempts: int = 5,
) -> list[Node]:
    """Generic post-node completion policy hook called by the scheduler.

    Evaluates:
      1. Failed VALIDATOR verdicts (passed==False)
      2. Failed BACKEND agent nodes (status==failed)
    If current attempt < max_attempts, creates a rework sub-chain for attempt N+1.
    """
    # Fetch all backend engineer nodes for this run to assess attempt count
    backend_stmt = (
        select(Node)
        .where(
            Node.run_id == run_id,
            Node.agent_role == "backend_engineer",
        )
    )
    backend_res = await session.execute(backend_stmt)
    backend_nodes = list(backend_res.scalars().all())
    if not backend_nodes:
        return []

    current_attempt = max(n.attempt for n in backend_nodes)

    # CASE A: Backend agent node execution failed
    if completed_node.agent_role == "backend_engineer" and completed_node.status == NodeStatus.failed:
        if current_attempt >= max_attempts:
            logger.info(f"Backend agent failed for run {run_id}, max attempts ({max_attempts}) reached. Ending rework.")
            return []
        next_attempt = current_attempt + 1
        logger.info(
            f"Backend agent node {completed_node.name} failed for run {run_id} (attempt {current_attempt}/{max_attempts}). Creating rework sub-chain for attempt {next_attempt}."
        )
        return await create_rework_subchain(
            session=session,
            run_id=run_id,
            trigger_node=completed_node,
            backend_nodes=backend_nodes,
            verdict_data={
                "passed": False,
                "failures": [f"agent_execution_failed: {completed_node.logs or 'LLM execution error'}"],
            },
            next_attempt=next_attempt,
            event_buffer=event_buffer,
        )

    # CASE B: Validator node finished
    if completed_node.node_type == NodeType.validator:
        # Fetch verdict artifact produced by this validator node
        stmt = (
            select(Artifact)
            .where(
                Artifact.run_id == run_id,
                Artifact.node_id == completed_node.id,
                Artifact.kind == "verdict",
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        verdict_art = result.scalar_one_or_none()

        if verdict_art is None:
            # Fallback check by run and attempt
            stmt_fb = (
                select(Artifact)
                .where(
                    Artifact.run_id == run_id,
                    Artifact.kind == "verdict",
                    Artifact.attempt == completed_node.attempt,
                )
                .order_by(Artifact.version.desc())
                .limit(1)
            )
            res_fb = await session.execute(stmt_fb)
            verdict_art = res_fb.scalar_one_or_none()

        if verdict_art is None:
            logger.warning(f"Validator node {completed_node.name} completed without emitting a verdict artifact.")
            return []

        # Parse verdict artifact content
        passed = False
        failures = []
        try:
            data = json.loads(verdict_art.content)
            if isinstance(data, dict):
                passed = bool(data.get("passed", False))
                failures = data.get("failures", [])
            elif isinstance(data, bool):
                passed = data
        except (json.JSONDecodeError, TypeError):
            raw_str = str(verdict_art.content).strip().lower()
            passed = (raw_str == "pass")

        if passed:
            logger.info(f"Validator node {completed_node.name} passed verdict for run {run_id}. Reviewer node will inspect working code.")
            return []

        # Objective Validator Failure: Cancel pending Reviewer node for this attempt so stuck-detection ignores it
        rev_stmt = (
            select(Node)
            .where(
                Node.run_id == run_id,
                Node.agent_role == "senior_reviewer",
                Node.attempt == completed_node.attempt,
                Node.status == NodeStatus.pending,
            )
        )
        rev_res = await session.execute(rev_stmt)
        reviewer_node = rev_res.scalar_one_or_none()
        if reviewer_node:
            await transition(
                session,
                reviewer_node,
                NodeStatus.cancelled,
                "Cancelled due to failing validator verdict",
                event_buffer,
            )

        if current_attempt >= max_attempts:
            logger.info(f"Validator verdict failed for run {run_id}, max attempts ({max_attempts}) reached. Ending rework.")
            return []

        next_attempt = current_attempt + 1
        logger.info(
            f"Validator verdict failed for run {run_id} (attempt {current_attempt}/{max_attempts}). Creating rework sub-chain for attempt {next_attempt}."
        )

        return await create_rework_subchain(
            session=session,
            run_id=run_id,
            trigger_node=completed_node,
            backend_nodes=backend_nodes,
            verdict_data={"passed": passed, "failures": failures},
            next_attempt=next_attempt,
            event_buffer=event_buffer,
        )

    # CASE C: Senior Reviewer agent node finished
    if completed_node.agent_role == "senior_reviewer" and completed_node.status == NodeStatus.completed:
        # Fetch review artifact produced by this reviewer node
        stmt = (
            select(Artifact)
            .where(
                Artifact.run_id == run_id,
                Artifact.node_id == completed_node.id,
                Artifact.kind == "review",
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        review_art = result.scalar_one_or_none()

        if review_art is None:
            stmt_fb = (
                select(Artifact)
                .where(
                    Artifact.run_id == run_id,
                    Artifact.kind == "review",
                    Artifact.attempt == completed_node.attempt,
                )
                .order_by(Artifact.version.desc())
                .limit(1)
            )
            res_fb = await session.execute(stmt_fb)
            review_art = res_fb.scalar_one_or_none()

        if review_art is None:
            logger.warning(f"Reviewer node {completed_node.name} completed without emitting a review artifact.")
            return []

        match = re.search(r"REVIEW_VERDICT:\s*(approved|changes_requested)", review_art.content, re.IGNORECASE)
        reviewer_verdict = match.group(1).lower() if match else "approved"

        if reviewer_verdict == "approved":
            logger.info(f"Senior Reviewer approved implementation for run {run_id} (attempt {current_attempt}). Work PASSED.")
            return []

        if current_attempt >= max_attempts:
            logger.info(f"Senior Reviewer requested changes for run {run_id}, max attempts ({max_attempts}) reached. Ending rework.")
            return []

        next_attempt = current_attempt + 1
        logger.info(
            f"Senior Reviewer requested changes for run {run_id} (attempt {current_attempt}/{max_attempts}). Creating rework sub-chain for attempt {next_attempt}."
        )

        return await create_rework_subchain(
            session=session,
            run_id=run_id,
            trigger_node=completed_node,
            backend_nodes=backend_nodes,
            verdict_data={"passed": True, "failures": [], "reviewer_verdict": "changes_requested", "review_content": review_art.content},
            next_attempt=next_attempt,
            event_buffer=event_buffer,
        )

    return []


async def create_rework_subchain(
    session: AsyncSession,
    run_id: uuid.UUID,
    trigger_node: Node,
    backend_nodes: list[Node],
    verdict_data: dict[str, Any],
    next_attempt: int,
    event_buffer: EventBuffer,
) -> list[Node]:
    """Instantiate a 4-node attempt sub-chain (Backend#(N+1) -> Executor#(N+1) -> Validator#(N+1) -> Reviewer#(N+1))."""
    prev_backend_node = max(backend_nodes, key=lambda n: getattr(n, "attempt", 1))
    prev_attempt = getattr(trigger_node, "attempt", 1)

    all_exec_stmt = (
        select(Node)
        .where(Node.run_id == run_id, Node.node_type == NodeType.executor)
        .order_by(Node.created_at.asc())
    )
    all_exec_res = await session.execute(all_exec_stmt)
    all_exec = list(all_exec_res.scalars().all())
    prev_exec_node = max(all_exec, key=lambda n: getattr(n, "attempt", 1)) if all_exec else None

    # Determine feedback artifact type: failure_context (objective runtime), review_feedback (subjective reviewer), or test_failure (contract test)
    is_test_rework = (
        trigger_node.agent_role == "test_validator"
        or (prev_exec_node and prev_exec_node.agent_role == "test_executor")
    )

    if trigger_node.agent_role == "senior_reviewer":
        fb_content = {
            "attempt": prev_attempt,
            "failed_role": "senior_reviewer",
            "reviewer_verdict": "changes_requested",
            "review_comments": verdict_data.get("review_content", ""),
        }
        fb_art_id = uuid.uuid4()
        fb_art = Artifact(
            id=fb_art_id,
            project_id=trigger_node.project_id,
            node_id=trigger_node.id,
            run_id=run_id,
            filename="review_feedback.json",
            kind="review_feedback",
            produced_by_role="senior_reviewer",
            content=json.dumps(fb_content, indent=2),
            content_type="application/json",
            version=1,
            attempt=next_attempt,
        )
        session.add(fb_art)
        buffer_artifact_created(
            event_buffer, run_id, trigger_node.id, "senior_reviewer",
            fb_art_id, "review_feedback.json", "review_feedback", "senior_reviewer", 1,
        )
    elif is_test_rework:
        report_data = {}
        if prev_exec_node:
            rep_stmt = (
                select(Artifact)
                .where(
                    Artifact.run_id == run_id,
                    Artifact.node_id == prev_exec_node.id,
                    Artifact.kind == "test_report",
                )
                .order_by(Artifact.version.desc())
                .limit(1)
            )
            rep_res = await session.execute(rep_stmt)
            rep_art = rep_res.scalar_one_or_none()
            if rep_art:
                try:
                    report_data = json.loads(rep_art.content)
                except Exception:
                    report_data = {"raw_content": rep_art.content}

        raw_pytest_out = report_data.get("pytest_output_tail", "") or trigger_node.logs or ""

        test_failure_summary = {
            "attempt": prev_attempt,
            "failed_role": "test_validator",
            "service_booted": report_data.get("service_booted", False),
            "tests_collected": report_data.get("tests_collected", 0),
            "passed": report_data.get("passed", 0),
            "failed": report_data.get("failed", 0),
            "failures": verdict_data.get("failures", []),
            "pytest_output_tail": raw_pytest_out[-3000:] if len(raw_pytest_out) > 3000 else raw_pytest_out,
        }

        tf_art_id = uuid.uuid4()
        tf_art = Artifact(
            id=tf_art_id,
            project_id=trigger_node.project_id,
            node_id=trigger_node.id,
            run_id=run_id,
            filename="test_failure.json",
            kind="test_failure",
            produced_by_role="test_validator",
            content=json.dumps(test_failure_summary, indent=2),
            content_type="application/json",
            version=1,
            attempt=next_attempt,
        )
        session.add(tf_art)
        buffer_artifact_created(
            event_buffer, run_id, trigger_node.id, "test_validator",
            tf_art_id, "test_failure.json", "test_failure", "test_validator", 1,
        )
    else:
        # Fetch execution_report produced by prev_exec_node for failure_context
        report_data = {}
        if prev_exec_node:
            rep_stmt = (
                select(Artifact)
                .where(
                    Artifact.run_id == run_id,
                    Artifact.node_id == prev_exec_node.id,
                    Artifact.kind == "execution_report",
                )
                .order_by(Artifact.version.desc())
                .limit(1)
            )
            rep_res = await session.execute(rep_stmt)
            rep_art = rep_res.scalar_one_or_none()
            if rep_art:
                try:
                    report_data = json.loads(rep_art.content)
                except Exception:
                    report_data = {"raw_content": rep_art.content}

        raw_container_logs = report_data.get("container_logs_tail", "") or trigger_node.logs or ""
        raw_build_logs = report_data.get("build_logs_tail", "") or ""

        failure_summary = {
            "attempt": prev_attempt,
            "failed_role": "backend_engineer",
            "build_success": report_data.get("build_success", False),
            "container_started": report_data.get("container_started", False),
            "health_ok": report_data.get("health_ok", False),
            "health_status_code": report_data.get("health_status_code"),
            "failures": verdict_data.get("failures", []),
            "container_logs_tail": raw_container_logs[-3000:] if len(raw_container_logs) > 3000 else raw_container_logs,
            "build_logs_tail": raw_build_logs[-1500:] if len(raw_build_logs) > 1500 else raw_build_logs,
        }

        failure_art_id = uuid.uuid4()
        failure_art = Artifact(
            id=failure_art_id,
            project_id=trigger_node.project_id,
            node_id=trigger_node.id,
            run_id=run_id,
            filename="failure_context.json",
            kind="failure_context",
            produced_by_role="validator" if trigger_node.node_type == NodeType.validator else "system",
            content=json.dumps(failure_summary, indent=2),
            content_type="application/json",
            version=1,
            attempt=next_attempt,
        )
        session.add(failure_art)
        buffer_artifact_created(
            event_buffer, run_id, trigger_node.id,
            "validator" if trigger_node.node_type == NodeType.validator else "system",
            failure_art_id, "failure_context.json", "failure_context",
            "validator" if trigger_node.node_type == NodeType.validator else "system", 1,
        )

    # 2. Create 4 new nodes: Backend#(N+1) -> Executor#(N+1) -> Validator#(N+1) -> Reviewer#(N+1)
    new_backend_id = uuid.uuid4()
    new_exec_id = uuid.uuid4()
    new_val_id = uuid.uuid4()
    new_rev_id = uuid.uuid4()

    new_backend_node = Node(
        id=new_backend_id,
        project_id=trigger_node.project_id,
        run_id=run_id,
        name=f"Backend_a{next_attempt}",
        node_type=NodeType.agent,
        agent_role="backend_engineer",
        status=NodeStatus.pending,
        attempt=next_attempt,
        rework_of_id=prev_backend_node.id,
        config={
            "required_inputs": [
                {"kind": "api_contract"},
                {"kind": "source_code", "optional": True},
                {"kind": "failure_context", "optional": True, "exact_attempt": True},
                {"kind": "review_feedback", "optional": True, "exact_attempt": True},
                {"kind": "test_failure", "optional": True, "exact_attempt": True},
            ]
        },
    )

    if is_test_rework:
        exec_config = {
            "required_inputs": [
                {"kind": "source_code", "exact_attempt": True},
                {"kind": "test_code"},  # pulls Attempt 1's test_code forward
            ]
        }
        if prev_exec_node and "mock_report" in prev_exec_node.config:
            mock_rep = dict(prev_exec_node.config["mock_report"])
            if prev_exec_node.config.get("mock_success_on_retry", False):
                mock_rep["service_booted"] = True
                mock_rep["passed"] = 3
                mock_rep["failed"] = 0
                mock_rep["pytest_output_tail"] = "3 passed in 0.15s"
            exec_config["mock_report"] = mock_rep
            exec_config["mock_success_on_retry"] = prev_exec_node.config.get("mock_success_on_retry", False)

        new_exec_node = Node(
            id=new_exec_id,
            project_id=trigger_node.project_id,
            run_id=run_id,
            name=f"TestExecutor_a{next_attempt}",
            node_type=NodeType.executor,
            agent_role="test_executor",
            status=NodeStatus.pending,
            attempt=next_attempt,
            rework_of_id=prev_exec_node.id if prev_exec_node else None,
            config=exec_config,
        )

        new_val_node = Node(
            id=new_val_id,
            project_id=trigger_node.project_id,
            run_id=run_id,
            name=f"TestValidator_a{next_attempt}",
            node_type=NodeType.validator,
            agent_role="test_validator",
            status=NodeStatus.pending,
            attempt=next_attempt,
            rework_of_id=trigger_node.id if trigger_node.node_type == NodeType.validator else None,
            config={"required_inputs": [{"kind": "test_report", "exact_attempt": True}]},
        )
    else:
        exec_config = {"required_inputs": [{"kind": "source_code", "exact_attempt": True}]}
        if prev_exec_node and "mock_report" in prev_exec_node.config:
            mock_rep = dict(prev_exec_node.config["mock_report"])
            if prev_exec_node.config.get("mock_success_on_retry", False):
                mock_rep["health_ok"] = True
                mock_rep["health_status_code"] = 200
                mock_rep["container_logs_tail"] = "Application started cleanly."
            exec_config["mock_report"] = mock_rep
            exec_config["mock_success_on_retry"] = prev_exec_node.config.get("mock_success_on_retry", False)

        new_exec_node = Node(
            id=new_exec_id,
            project_id=trigger_node.project_id,
            run_id=run_id,
            name=f"BackendExecutor_a{next_attempt}",
            node_type=NodeType.executor,
            agent_role="backend_executor",
            status=NodeStatus.pending,
            attempt=next_attempt,
            rework_of_id=prev_exec_node.id if prev_exec_node else None,
            config=exec_config,
        )

        new_val_node = Node(
            id=new_val_id,
            project_id=trigger_node.project_id,
            run_id=run_id,
            name=f"Validator_a{next_attempt}",
            node_type=NodeType.validator,
            agent_role="validator",
            status=NodeStatus.pending,
            attempt=next_attempt,
            rework_of_id=trigger_node.id if trigger_node.node_type == NodeType.validator else None,
            config={"required_inputs": [{"kind": "execution_report", "exact_attempt": True}]},
        )

    new_rev_node = Node(
        id=new_rev_id,
        project_id=trigger_node.project_id,
        run_id=run_id,
        name=f"Reviewer_a{next_attempt}",
        node_type=NodeType.agent,
        agent_role="senior_reviewer",
        status=NodeStatus.pending,
        attempt=next_attempt,
        rework_of_id=trigger_node.id if trigger_node.agent_role == "senior_reviewer" else None,
        config={
            "required_inputs": [
                {"kind": "verdict", "exact_attempt": True},
                {"kind": "source_code", "exact_attempt": True},
                {"kind": "api_contract"},
            ]
        },
    )

    session.add_all([new_backend_node, new_exec_node, new_val_node, new_rev_node])

    # Add NodeDependency edges
    dep1 = NodeDependency(node_id=new_backend_id, depends_on_node_id=trigger_node.id)
    dep2 = NodeDependency(node_id=new_exec_id, depends_on_node_id=new_backend_id)
    dep3 = NodeDependency(node_id=new_val_id, depends_on_node_id=new_exec_id)
    dep4 = NodeDependency(node_id=new_rev_id, depends_on_node_id=new_val_id)
    session.add_all([dep1, dep2, dep3, dep4])

    # Buffer node_created events
    for n in [new_backend_node, new_exec_node, new_val_node, new_rev_node]:
        buffer_node_created(
            event_buffer,
            run_id,
            n.id,
            n.name,
            n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
            n.status.value,
        )

    return [new_backend_node, new_exec_node, new_val_node, new_rev_node]
