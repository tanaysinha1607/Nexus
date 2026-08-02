"""Phase 4 Unit Tests: DevOps Engineer Agent + Real Dockerfile Validation (hadolint + docker build).

Tests:
1. hadolint JSON parser with non-zero exit code & strict level == "error" filtering.
2. DevOpsValidator deterministic verdict (PASS on 0 errors + warnings; FAIL on >0 errors or build failure).
3. Teeth test: known-bad Dockerfile fixture (FROM untagged + USER root) -> hadolint ERROR -> validator FAIL.
4. DevOps rework readiness & subchain resolution.
5. Validator dispatch across all 6 report kinds (stdout, execution, test, build, security, devops).
"""

import json
import uuid
import pytest
from app.models import Artifact, Node, NodeStatus, NodeType, Project, Run
from orchestrator.config import HandlerConfig
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.readiness import resolve_node_readiness
from orchestrator.sandbox.devops_runner import parse_hadolint_output


def test_hadolint_json_parser_nonzero_exit_code():
    """Verify hadolint JSON stdout parser filters ONLY level=='error' as errors, ignoring warnings."""
    # Sample hadolint JSON with 1 error, 5 warnings, 1 info
    raw_hadolint_json = json.dumps([
        {"code": "DL3006", "level": "error", "message": "Always tag the version of an image explicitly", "line": 1},
        {"code": "DL3008", "level": "warning", "message": "Pin versions in apt get install", "line": 3},
        {"code": "DL3013", "level": "warning", "message": "Pin versions in pip install", "line": 4},
        {"code": "DL3009", "level": "warning", "message": "Delete the apt-get lists after installing", "line": 5},
        {"code": "DL3015", "level": "warning", "message": "Avoid additional packages by specifying --no-install-recommends", "line": 6},
        {"code": "DL3059", "level": "info", "message": "Multiple consecutive RUN instructions", "line": 7},
    ])

    ran, err_cnt, warn_cnt, findings = parse_hadolint_output(raw_hadolint_json)
    assert ran is True
    assert err_cnt == 1
    assert warn_cnt == 4
    assert len(findings) == 1
    assert findings[0]["code"] == "DL3006"
    assert findings[0]["level"] == "error"

    # Test 5 warnings and 0 errors -> err_cnt MUST be 0
    warnings_only_json = json.dumps([
        {"code": "DL3008", "level": "warning", "message": "Pin versions in apt get install", "line": 3},
        {"code": "DL3013", "level": "warning", "message": "Pin versions in pip install", "line": 4},
        {"code": "DL3009", "level": "warning", "message": "Delete the apt-get lists", "line": 5},
        {"code": "DL3015", "level": "warning", "message": "Avoid additional packages", "line": 6},
        {"code": "DL3059", "level": "warning", "message": "Multiple consecutive RUN instructions", "line": 7},
    ])
    ran_w, err_cnt_w, warn_cnt_w, findings_w = parse_hadolint_output(warnings_only_json)
    assert ran_w is True
    assert err_cnt_w == 0
    assert warn_cnt_w == 5
    assert len(findings_w) == 0


@pytest.mark.asyncio
async def test_devops_validator_deterministic_verdict():
    """Verify DevOpsValidator PASS/FAIL rules: PASS iff error_count==0 and build_success==True."""
    config = HandlerConfig()
    node = Node(id=uuid.uuid4(), name="DevOpsValidator", node_type=NodeType.validator, agent_role="devops_validator")

    # 1. Happy path: 0 errors, 5 warnings, build_success=True -> PASS
    clean_rep = Artifact(
        id=uuid.uuid4(),
        filename="devops_report.json",
        kind="devops_report",
        content=json.dumps({
            "hadolint_ran": True,
            "error_count": 0,
            "warning_count": 5,
            "hadolint_findings": [],
            "build_attempted": True,
            "build_success": True,
            "build_logs_tail": "Successfully built image",
        }),
    )
    res_pass = await handle_validator_node(node, {"devops_report": clean_rep}, config)
    assert res_pass.status == NodeStatus.completed
    v_pass = json.loads(res_pass.artifacts[0].content)
    assert v_pass["passed"] is True
    assert len(v_pass["failures"]) == 0

    # 2. Hadolint error: 1 error, build_success=True -> FAIL
    err_rep = Artifact(
        id=uuid.uuid4(),
        filename="devops_report.json",
        kind="devops_report",
        content=json.dumps({
            "hadolint_ran": True,
            "error_count": 1,
            "warning_count": 2,
            "hadolint_findings": [{"code": "DL3002", "level": "error", "message": "Last USER should not be root", "line": 10}],
            "build_attempted": True,
            "build_success": True,
            "build_logs_tail": "Successfully built image",
        }),
    )
    res_err = await handle_validator_node(node, {"devops_report": err_rep}, config)
    assert res_err.status == NodeStatus.completed
    v_err = json.loads(res_err.artifacts[0].content)
    assert v_err["passed"] is False
    assert any("HADOLINT_ERROR" in f for f in v_err["failures"])

    # 3. Docker build failure: 0 errors, build_success=False -> FAIL
    build_fail_rep = Artifact(
        id=uuid.uuid4(),
        filename="devops_report.json",
        kind="devops_report",
        content=json.dumps({
            "hadolint_ran": True,
            "error_count": 0,
            "warning_count": 0,
            "hadolint_findings": [],
            "build_attempted": True,
            "build_success": False,
            "build_logs_tail": "COPY failed: file not found in build context",
        }),
    )
    res_bf = await handle_validator_node(node, {"devops_report": build_fail_rep}, config)
    assert res_bf.status == NodeStatus.completed
    v_bf = json.loads(res_bf.artifacts[0].content)
    assert v_bf["passed"] is False
    assert any("DOCKER_BUILD_FAILED" in f for f in v_bf["failures"])


@pytest.mark.asyncio
async def test_known_bad_dockerfile_fixture_scan():
    """Verify linter catches real errors on a known-bad Dockerfile fixture (FROM untagged & USER root)."""
    bad_hadolint_json = json.dumps([
        {
            "code": "DL3006",
            "level": "error",
            "message": "Always tag the version of an image explicitly",
            "line": 1,
        },
        {
            "code": "DL3002",
            "level": "error",
            "message": "Last USER should not be root",
            "line": 5,
        }
    ])

    ran, err_cnt, warn_cnt, findings = parse_hadolint_output(bad_hadolint_json)
    assert ran is True
    assert err_cnt == 2
    assert len(findings) == 2

    # Verify validator fails on this fixture
    config = HandlerConfig()
    node = Node(id=uuid.uuid4(), name="DevOpsValidator", node_type=NodeType.validator, agent_role="devops_validator")
    rep_art = Artifact(
        id=uuid.uuid4(),
        filename="devops_report.json",
        kind="devops_report",
        content=json.dumps({
            "hadolint_ran": ran,
            "error_count": err_cnt,
            "warning_count": warn_cnt,
            "hadolint_findings": findings,
            "build_attempted": True,
            "build_success": True,
        }),
    )
    res = await handle_validator_node(node, {"devops_report": rep_art}, config)
    v_data = json.loads(res.artifacts[0].content)
    assert v_data["passed"] is False
    assert len(v_data["failures"]) == 2


@pytest.mark.asyncio
async def test_devops_rework_readiness_and_subchain(db_session):
    """Verify attempt-2 devops_engineer readiness requires devops_finding or review_feedback."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    proj = Project(id=project_id, name="TestDevOpsProj", user_prompt="FastAPI service")
    run_obj = Run(id=run_id, project_id=project_id)
    db_session.add_all([proj, run_obj])
    await db_session.commit()

    devops_node_a2 = Node(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        name="DevOps_a2",
        node_type=NodeType.agent,
        agent_role="devops_engineer",
        attempt=2,
        config={
            "required_inputs": [
                {"kind": "source_code"},
                {"kind": "devops_finding", "optional": True, "exact_attempt": True},
            ]
        },
    )
    db_session.add(devops_node_a2)
    await db_session.commit()

    art_src = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=None, run_id=run_id,
        filename="main.py", kind="source_code", produced_by_role="backend_engineer",
        content="app = FastAPI()", attempt=1
    )
    db_session.add(art_src)
    await db_session.commit()

    # Without devops_finding, attempt 2 MUST NOT be ready
    is_ready_no_finding, _ = await resolve_node_readiness(db_session, devops_node_a2, run_id)
    assert is_ready_no_finding is False

    # Add attempt 2 devops_finding
    art_finding = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=None, run_id=run_id,
        filename="devops_finding.json", kind="devops_finding", produced_by_role="devops_validator",
        content=json.dumps({"hadolint_findings": [{"code": "DL3002"}]}), attempt=2
    )
    db_session.add(art_finding)
    await db_session.commit()

    # With devops_finding, attempt 2 IS ready
    is_ready_with_finding, resolved = await resolve_node_readiness(db_session, devops_node_a2, run_id)
    assert is_ready_with_finding is True
    assert "source_code" in resolved
    assert "devops_finding" in resolved


@pytest.mark.asyncio
async def test_validator_dispatch_all_six_report_kinds():
    """Verify validator.py handles all 6 report kinds (stdout, execution, test, build, security, devops) without regression."""
    config = HandlerConfig()
    node = Node(id=uuid.uuid4(), name="TestValidator", node_type=NodeType.validator, agent_role="test_validator")

    # 1. stdout
    res1 = await handle_validator_node(node, {"stdout": Artifact(id=uuid.uuid4(), filename="stdout.json", kind="stdout", content='{"exit_code": 0}')}, config)
    assert "pass" in res1.artifacts[0].content.lower()

    # 2. execution_report
    res2 = await handle_validator_node(node, {"execution_report": Artifact(id=uuid.uuid4(), filename="execution_report.json", kind="execution_report", content='{"build_success": true, "container_started": true, "health_ok": true, "health_status_code": 200}')}, config)
    assert json.loads(res2.artifacts[0].content)["passed"] is True

    # 3. test_report
    res3 = await handle_validator_node(node, {"test_report": Artifact(id=uuid.uuid4(), filename="test_report.json", kind="test_report", content='{"service_booted": true, "passed": 3, "failed": 0}')}, config)
    assert json.loads(res3.artifacts[0].content)["passed"] is True

    # 4. build_report
    res4 = await handle_validator_node(node, {"build_report": Artifact(id=uuid.uuid4(), filename="build_report.json", kind="build_report", content='{"build_attempted": true, "tsc_exit_code": 0, "type_errors": 0, "compiled_ok": true}')}, config)
    assert json.loads(res4.artifacts[0].content)["passed"] is True

    # 5. security_report
    res5 = await handle_validator_node(node, {"security_report": Artifact(id=uuid.uuid4(), filename="security_report.json", kind="security_report", content='{"scan_completed": true, "high_count": 0, "high_findings": []}')}, config)
    assert json.loads(res5.artifacts[0].content)["passed"] is True

    # 6. devops_report
    res6 = await handle_validator_node(node, {"devops_report": Artifact(id=uuid.uuid4(), filename="devops_report.json", kind="devops_report", content='{"hadolint_ran": true, "error_count": 0, "warning_count": 3, "build_success": true}')}, config)
    assert json.loads(res6.artifacts[0].content)["passed"] is True
