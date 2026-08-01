"""Phase 2a Unit Tests: QA Engineer Agent & Contract-Based Test Execution."""

import json
import pytest
import uuid
from app.models import Artifact, Node, NodeStatus, NodeType, Project, Run
from orchestrator.agents.roles import ROLES
from orchestrator.config import HandlerConfig, SchedulerConfig
from orchestrator.handlers import ArtifactSpec
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.readiness import resolve_node_readiness
from orchestrator.sandbox.test_runner import parse_pytest_stdout


def test_qa_engineer_role_definition():
    """Verify qa_engineer role configuration, input selectors, and outputs."""
    assert "qa_engineer" in ROLES
    role = ROLES["qa_engineer"]
    assert role.name == "qa_engineer"
    assert role.max_tokens == 3000
    assert len(role.input_selectors) == 1
    assert role.input_selectors[0] == {"kind": "api_contract"}
    assert len(role.outputs) == 1
    assert role.outputs[0].kind == "test_code"
    assert role.outputs[0].filename == "test_api.py"


def test_pytest_stdout_parser():
    """Verify pytest output parsing for collected, passed, and failed counts."""
    out1 = "collected 3 items\n\ntest_api.py ... [100%]\n\n================ 3 passed in 0.05s ================"
    col, p, f = parse_pytest_stdout(out1)
    assert col == 3
    assert p == 3
    assert f == 0

    out2 = "collected 4 items\n\ntest_api.py ..F. [100%]\n\n================ 3 passed, 1 failed in 0.12s ================"
    col, p, f = parse_pytest_stdout(out2)
    assert col == 4
    assert p == 3
    assert f == 1

    out3 = "Service failed to boot"
    col, p, f = parse_pytest_stdout(out3)
    assert col == 0
    assert p == 0
    assert f == 0


@pytest.mark.asyncio
async def test_test_validator_deterministic_verdict():
    """Verify TestValidator logic for test_report input artifacts."""
    node = Node(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        name="TestValidator",
        node_type=NodeType.validator,
        agent_role="test_validator",
        config={},
    )
    config = HandlerConfig()

    # Case A: Service booted, 3 passed, 0 failed -> PASS
    pass_report = {
        "service_booted": True,
        "tests_collected": 3,
        "passed": 3,
        "failed": 0,
        "pytest_output_tail": "3 passed in 0.10s",
    }
    art_pass = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="test_report.json",
        kind="test_report",
        content=json.dumps(pass_report),
    )

    res_pass = await handle_validator_node(node, {"test_report": art_pass}, config)
    assert res_pass.status == NodeStatus.completed
    v_data = json.loads(res_pass.artifacts[0].content)
    assert v_data["passed"] is True
    assert len(v_data["failures"]) == 0

    # Case B: 1 test failed -> FAIL
    fail_report = {
        "service_booted": True,
        "tests_collected": 3,
        "passed": 2,
        "failed": 1,
        "pytest_output_tail": "2 passed, 1 failed in 0.15s",
    }
    art_fail = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="test_report.json",
        kind="test_report",
        content=json.dumps(fail_report),
    )

    res_fail = await handle_validator_node(node, {"test_report": art_fail}, config)
    assert res_fail.status == NodeStatus.completed
    v_fail_data = json.loads(res_fail.artifacts[0].content)
    assert v_fail_data["passed"] is False
    assert "1_tests_failed" in v_fail_data["failures"]


def test_scheduler_config_forces_serial_for_real_agents():
    """Verify SchedulerConfig enforces max_parallel_nodes=1 when use_real_agents=True."""
    cfg = SchedulerConfig(use_real_agents=True, max_parallel_nodes=4)
    assert cfg.max_parallel_nodes == 1


@pytest.mark.asyncio
async def test_attempt_2_test_code_carry_forward(db_session):
    """Verify attempt-2 TestExecutor resolves Attempt 1's test_code + Attempt 2's source_code."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    proj = Project(id=project_id, name="TestProj", user_prompt="Build auth service")
    run_obj = Run(id=run_id, project_id=project_id)
    
    node_qa_a1 = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="QA_a1", node_type=NodeType.agent, agent_role="qa_engineer", attempt=1)
    node_be_a1 = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Backend_a1", node_type=NodeType.agent, agent_role="backend_engineer", attempt=1)
    node_be_a2 = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Backend_a2", node_type=NodeType.agent, agent_role="backend_engineer", attempt=2)

    db_session.add_all([proj, run_obj, node_qa_a1, node_be_a1, node_be_a2])
    await db_session.flush()

    # Attempt 1 Artifacts
    art_test_a1 = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=node_qa_a1.id,
        run_id=run_id,
        filename="test_api.py",
        kind="test_code",
        produced_by_role="qa_engineer",
        content="def test_auth(): assert True",
        attempt=1,
    )
    art_src_a1 = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=node_be_a1.id,
        run_id=run_id,
        filename="main.py",
        kind="source_code",
        produced_by_role="backend_engineer",
        content="# attempt 1 code",
        attempt=1,
    )
    # Attempt 2 Artifacts
    art_src_a2 = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=node_be_a2.id,
        run_id=run_id,
        filename="main.py",
        kind="source_code",
        produced_by_role="backend_engineer",
        content="# attempt 2 code",
        attempt=2,
    )

    db_session.add_all([art_test_a1, art_src_a1, art_src_a2])
    await db_session.commit()

    # Attempt 2 TestExecutor Node
    test_exec_a2 = Node(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        name="TestExecutor_a2",
        node_type=NodeType.executor,
        agent_role="test_executor",
        status=NodeStatus.ready,
        attempt=2,
        config={
            "required_inputs": [
                {"kind": "source_code", "exact_attempt": True},
                {"kind": "test_code"},
            ]
        },
    )
    db_session.add(test_exec_a2)
    await db_session.commit()

    is_ready, resolved = await resolve_node_readiness(db_session, test_exec_a2, run_id)
    assert is_ready is True
    assert "source_code" in resolved
    assert "test_code" in resolved
    assert resolved["source_code"].attempt == 2
    assert resolved["source_code"].content == "# attempt 2 code"
    assert resolved["test_code"].attempt == 1
    assert resolved["test_code"].content == "def test_auth(): assert True"
