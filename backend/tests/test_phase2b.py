"""Phase 2b Unit Tests: Frontend Engineer Agent & TypeScript Compiler Validation."""

import json
import pytest
import uuid
from app.models import Artifact, Node, NodeStatus, NodeType, Project, Run
from orchestrator.agents.roles import ROLES
from orchestrator.config import HandlerConfig
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.readiness import resolve_node_readiness
from orchestrator.sandbox.ts_build_runner import parse_tsc_stdout


def test_frontend_engineer_role_definition():
    """Verify frontend_engineer role configuration, input selectors, and outputs."""
    assert "frontend_engineer" in ROLES
    role = ROLES["frontend_engineer"]
    assert role.name == "frontend_engineer"
    assert role.max_tokens == 3000
    assert len(role.input_selectors) == 2
    assert role.input_selectors[0] == {"kind": "api_contract"}
    assert role.input_selectors[1] == {"kind": "build_failure"}
    assert len(role.outputs) >= 1
    assert role.outputs[0].kind == "frontend_code"
    assert role.outputs[0].filename == "client.ts"


def test_tsc_stdout_parser():
    """Verify tsc output parsing for error count and compiled_ok status."""
    # Clean compile
    out1 = "Found 0 errors."
    errs1, ok1 = parse_tsc_stdout(out1)
    assert errs1 == 0
    assert ok1 is True

    # Error output
    out2 = "client.ts(14,3): error TS2322: Type 'number' is not assignable to type 'string'.\nFound 1 error."
    errs2, ok2 = parse_tsc_stdout(out2)
    assert errs2 == 1
    assert ok2 is False

    # Empty stdout
    errs3, ok3 = parse_tsc_stdout("")
    assert errs3 == 0
    assert ok3 is True


@pytest.mark.asyncio
async def test_build_validator_deterministic_verdict():
    """Verify BuildValidator logic for build_report input artifacts."""
    node = Node(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        name="BuildValidator",
        node_type=NodeType.validator,
        agent_role="build_validator",
        config={},
    )
    config = HandlerConfig()

    # Case A: Clean compile -> PASS
    pass_report = {
        "build_attempted": True,
        "tsc_exit_code": 0,
        "type_errors": 0,
        "compiled_ok": True,
        "tsc_output_tail": "Found 0 errors.",
    }
    art_pass = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="build_report.json",
        kind="build_report",
        content=json.dumps(pass_report),
    )

    res_pass = await handle_validator_node(node, {"build_report": art_pass}, config)
    assert res_pass.status == NodeStatus.completed
    v_data = json.loads(res_pass.artifacts[0].content)
    assert v_data["passed"] is True
    assert len(v_data["failures"]) == 0

    # Case B: 1 type error -> FAIL
    fail_report = {
        "build_attempted": True,
        "tsc_exit_code": 1,
        "type_errors": 1,
        "compiled_ok": False,
        "tsc_output_tail": "client.ts(10,5): error TS2322: Type 'number' is not assignable to type 'string'.",
    }
    art_fail = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="build_report.json",
        kind="build_report",
        content=json.dumps(fail_report),
    )

    res_fail = await handle_validator_node(node, {"build_report": art_fail}, config)
    assert res_fail.status == NodeStatus.completed
    v_fail_data = json.loads(res_fail.artifacts[0].content)
    assert v_fail_data["passed"] is False
    assert len(v_fail_data["failures"]) > 0


@pytest.mark.asyncio
async def test_frontend_engineer_rework_readiness(db_session):
    """Verify attempt-2 Frontend Engineer is ready when build_failure is present."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    proj = Project(id=project_id, name="TestProj", user_prompt="Build API client")
    run_obj = Run(id=run_id, project_id=project_id)
    node_api = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="ApiDesigner", node_type=NodeType.agent, agent_role="api_designer", attempt=1)
    node_val = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="BuildValidator_a1", node_type=NodeType.validator, agent_role="build_validator", attempt=1)

    db_session.add_all([proj, run_obj, node_api, node_val])
    await db_session.flush()

    # Attempt 1 Artifacts
    art_contract = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=node_api.id,
        run_id=run_id,
        filename="api_contract.json",
        kind="api_contract",
        produced_by_role="api_designer",
        content="{}",
        attempt=1,
    )
    art_bf_a2 = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        node_id=node_val.id,
        run_id=run_id,
        filename="build_failure.json",
        kind="build_failure",
        produced_by_role="build_validator",
        content=json.dumps({"type_errors": 1, "tsc_output_tail": "error TS2322"}),
        attempt=2,
    )

    db_session.add_all([art_contract, art_bf_a2])
    await db_session.commit()

    # Attempt 2 Frontend Engineer Node
    fe_node_a2 = Node(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        name="Frontend_a2",
        node_type=NodeType.agent,
        agent_role="frontend_engineer",
        status=NodeStatus.ready,
        attempt=2,
        config={
            "required_inputs": [
                {"kind": "api_contract"},
                {"kind": "build_failure", "optional": True, "exact_attempt": True},
            ]
        },
    )
    db_session.add(fe_node_a2)
    await db_session.commit()

    is_ready, resolved = await resolve_node_readiness(db_session, fe_node_a2, run_id)
    assert is_ready is True
    assert "api_contract" in resolved
    assert "build_failure" in resolved
    assert resolved["build_failure"].attempt == 2
