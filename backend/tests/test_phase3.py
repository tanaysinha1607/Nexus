"""Phase 3 Unit Tests: Security Agent & Bandit AST Scanner Integration."""

import json
import pytest
import uuid
from app.models import Artifact, Node, NodeStatus, NodeType, Project, Run
from orchestrator.config import HandlerConfig
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.readiness import resolve_node_readiness
from orchestrator.sandbox.bandit_runner import parse_bandit_stdout


def test_bandit_json_parser_nonzero_exit_code():
    """Verify bandit JSON stdout parsing works regardless of non-zero container exit code.
    
    Bandit exits non-zero whenever vulnerabilities are found.
    Valid JSON returned means scan_completed=True!
    """
    raw_bandit_stdout = json.dumps({
        "errors": [],
        "generated_at": "2026-08-01T20:00:00Z",
        "results": [
            {
                "code": "1 DB_PASSWORD = \"hardcoded_secret_123\"\n",
                "filename": "main.py",
                "issue_confidence": "HIGH",
                "issue_cwe": {"id": 259, "link": "https://cwe.mitre.org/data/definitions/259.html"},
                "issue_severity": "HIGH",
                "issue_text": "Possible hardcoded password: 'hardcoded_secret_123'",
                "line_number": 1,
                "line_range": [1],
                "more_info": "https://bandit.readthedocs.io/en/1.7.9/plugins/b105_hardcoded_password_string.html",
                "test_id": "B105",
                "test_name": "hardcoded_password_string"
            }
        ]
    })

    scan_completed, high_count, medium_count, low_count, high_findings, version = parse_bandit_stdout(raw_bandit_stdout)

    assert scan_completed is True
    assert high_count == 1
    assert medium_count == 0
    assert low_count == 0
    assert len(high_findings) == 1
    assert high_findings[0]["test_id"] == "B105"
    assert high_findings[0]["filename"] == "main.py"
    assert high_findings[0]["line_number"] == 1


@pytest.mark.asyncio
async def test_security_validator_deterministic_verdict():
    """Verify SecurityValidator logic for security_report input artifacts."""
    node = Node(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        name="SecurityValidator",
        node_type=NodeType.validator,
        agent_role="security_validator",
        config={},
    )
    config = HandlerConfig()

    # Case A: 0 HIGH vulnerabilities -> PASS
    pass_report = {
        "scan_completed": True,
        "high_count": 0,
        "medium_count": 1,
        "low_count": 2,
        "high_findings": [],
        "bandit_version": "1.7.9",
    }
    art_pass = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="security_report.json",
        kind="security_report",
        content=json.dumps(pass_report),
    )

    res_pass = await handle_validator_node(node, {"security_report": art_pass}, config)
    assert res_pass.status == NodeStatus.completed
    v_pass = json.loads(res_pass.artifacts[0].content)
    assert v_pass["passed"] is True
    assert len(v_pass["failures"]) == 0

    # Case B: 1 HIGH vulnerability (B105) -> FAIL
    fail_report = {
        "scan_completed": True,
        "high_count": 1,
        "medium_count": 0,
        "low_count": 0,
        "high_findings": [
            {
                "test_id": "B105",
                "issue_text": "Possible hardcoded password: 'secret'",
                "filename": "main.py",
                "line_number": 15,
            }
        ],
        "bandit_version": "1.7.9",
    }
    art_fail = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="security_report.json",
        kind="security_report",
        content=json.dumps(fail_report),
    )

    res_fail = await handle_validator_node(node, {"security_report": art_fail}, config)
    assert res_fail.status == NodeStatus.completed
    v_fail = json.loads(res_fail.artifacts[0].content)
    assert v_fail["passed"] is False
    assert len(v_fail["failures"]) == 1
    assert "HIGH_VULNERABILITY [B105]" in v_fail["failures"][0]

    # Case C: scan_completed = False -> FAIL
    incomplete_report = {
        "scan_completed": False,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "high_findings": [],
    }
    art_inc = Artifact(
        id=uuid.uuid4(),
        project_id=node.project_id,
        node_id=uuid.uuid4(),
        run_id=node.run_id,
        filename="security_report.json",
        kind="security_report",
        content=json.dumps(incomplete_report),
    )

    res_inc = await handle_validator_node(node, {"security_report": art_inc}, config)
    assert res_inc.status == NodeStatus.completed
    v_inc = json.loads(res_inc.artifacts[0].content)
    assert v_inc["passed"] is False
    assert "security_scan_failed_to_complete" in v_inc["failures"]


@pytest.mark.asyncio
async def test_known_vulnerable_code_fixture_scan():
    """Verify scanner catches real vulnerabilities on a known-vulnerable code fixture (B105 hardcoded password & B307 eval)."""
    vulnerable_code = """
import os

DB_PASSWORD = "super_secret_hardcoded_password_123"

def run_user_code(user_input: str):
    return eval(user_input)
"""

    # Mock bandit output for this fixture
    fixture_json = json.dumps({
        "results": [
            {
                "test_id": "B105",
                "issue_severity": "HIGH",
                "issue_text": "Possible hardcoded password",
                "filename": "main.py",
                "line_number": 4,
            },
            {
                "test_id": "B307",
                "issue_severity": "HIGH",
                "issue_text": "Use of possibly insecure function - eval",
                "filename": "main.py",
                "line_number": 7,
            }
        ]
    })

    scan_ok, high_cnt, med_cnt, low_cnt, findings, ver = parse_bandit_stdout(fixture_json)
    assert scan_ok is True
    assert high_cnt == 2
    assert findings[0]["test_id"] == "B105"
    assert findings[1]["test_id"] == "B307"

    node = Node(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
        name="SecurityValidator", node_type=NodeType.validator, agent_role="security_validator", config={}
    )
    art = Artifact(
        id=uuid.uuid4(), project_id=node.project_id, node_id=uuid.uuid4(), run_id=node.run_id,
        filename="security_report.json", kind="security_report",
        content=json.dumps({"scan_completed": scan_ok, "high_count": high_cnt, "high_findings": findings})
    )

    res = await handle_validator_node(node, {"security_report": art}, HandlerConfig())
    v_data = json.loads(res.artifacts[0].content)
    assert v_data["passed"] is False
    assert len(v_data["failures"]) == 2


@pytest.mark.asyncio
async def test_backend_engineer_security_rework_readiness(db_session):
    """Verify attempt-2 Backend Engineer is ready when security_finding is present."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    proj = Project(id=project_id, name="TestSecurityProj", user_prompt="Build auth API")
    run_obj = Run(id=run_id, project_id=project_id)
    node_api = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="ApiDesigner", node_type=NodeType.agent, agent_role="api_designer", attempt=1)
    node_val = Node(id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="SecurityValidator_a1", node_type=NodeType.validator, agent_role="security_validator", attempt=1)

    db_session.add_all([proj, run_obj, node_api, node_val])
    await db_session.flush()

    art_contract = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=node_api.id, run_id=run_id,
        filename="api_contract.json", kind="api_contract", produced_by_role="api_designer", content="{}", attempt=1
    )
    art_sf_a2 = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=node_val.id, run_id=run_id,
        filename="security_finding.json", kind="security_finding", produced_by_role="security_validator",
        content=json.dumps({"high_count": 1, "high_findings": [{"test_id": "B105", "filename": "main.py", "line_number": 4}]}), attempt=2
    )

    db_session.add_all([art_contract, art_sf_a2])
    await db_session.commit()

    be_node_a2 = Node(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Backend_a2",
        node_type=NodeType.agent, agent_role="backend_engineer", status=NodeStatus.ready, attempt=2,
        config={
            "required_inputs": [
                {"kind": "api_contract"},
                {"kind": "security_finding", "optional": True, "exact_attempt": True},
            ]
        },
    )
    db_session.add(be_node_a2)
    await db_session.commit()

    is_ready, resolved = await resolve_node_readiness(db_session, be_node_a2, run_id)
    assert is_ready is True
    assert "api_contract" in resolved
    assert "security_finding" in resolved
    assert resolved["security_finding"].attempt == 2


@pytest.mark.asyncio
async def test_security_rework_subchain_readiness_and_attempt_carry_forward(db_session):
    """Verify attempt-2 SecurityScanExecutor, SecurityValidator, and Reviewer resolve artifacts cleanly."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    proj = Project(id=project_id, name="TestSubchainProj", user_prompt="Auth API")
    run_obj = Run(id=run_id, project_id=project_id)
    db_session.add_all([proj, run_obj])
    await db_session.commit()

    # Attempt 2 SecurityScanExecutor node
    sec_exec_a2 = Node(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="SecurityScanExecutor_a2",
        node_type=NodeType.executor, agent_role="security_executor", attempt=2,
        config={"required_inputs": [{"kind": "source_code", "exact_attempt": True}]}
    )
    # Attempt 2 SecurityValidator node
    sec_val_a2 = Node(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="SecurityValidator_a2",
        node_type=NodeType.validator, agent_role="security_validator", attempt=2,
        config={"required_inputs": [{"kind": "security_report", "exact_attempt": True}]}
    )
    # Attempt 2 Reviewer node
    rev_a2 = Node(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Reviewer_a2",
        node_type=NodeType.agent, agent_role="senior_reviewer", attempt=2,
        config={
            "required_inputs": [
                {"kind": "verdict", "exact_attempt": True},
                {"kind": "source_code", "exact_attempt": True},
                {"kind": "api_contract"},
            ]
        }
    )
    db_session.add_all([sec_exec_a2, sec_val_a2, rev_a2])
    await db_session.commit()

    # Add Attempt 2 source_code
    art_src_a2 = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=sec_exec_a2.id, run_id=run_id,
        filename="main.py", kind="source_code", produced_by_role="backend_engineer",
        content="app = FastAPI()", attempt=2
    )
    art_contract_a1 = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=None, run_id=run_id,
        filename="api_contract.json", kind="api_contract", produced_by_role="api_designer",
        content="{}", attempt=1
    )
    db_session.add_all([art_src_a2, art_contract_a1])
    await db_session.commit()

    # 1. Check SecurityScanExecutor_a2 readiness
    is_ready_exec, resolved_exec = await resolve_node_readiness(db_session, sec_exec_a2, run_id)
    assert is_ready_exec is True
    assert "source_code" in resolved_exec
    assert resolved_exec["source_code"].attempt == 2

    # Add Attempt 2 security_report
    art_report_a2 = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=sec_exec_a2.id, run_id=run_id,
        filename="security_report.json", kind="security_report", produced_by_role="security_executor",
        content=json.dumps({"scan_completed": True, "high_count": 0, "high_findings": []}), attempt=2
    )
    db_session.add(art_report_a2)
    await db_session.commit()

    # 2. Check SecurityValidator_a2 readiness
    is_ready_val, resolved_val = await resolve_node_readiness(db_session, sec_val_a2, run_id)
    assert is_ready_val is True
    assert "security_report" in resolved_val
    assert resolved_val["security_report"].attempt == 2

    # Add Attempt 2 verdict
    art_verdict_a2 = Artifact(
        id=uuid.uuid4(), project_id=project_id, node_id=sec_val_a2.id, run_id=run_id,
        filename="verdict.json", kind="verdict", produced_by_role="security_validator",
        content=json.dumps({"passed": True, "failures": []}), attempt=2
    )
    db_session.add(art_verdict_a2)
    await db_session.commit()

    # 3. Check Reviewer_a2 readiness
    is_ready_rev, resolved_rev = await resolve_node_readiness(db_session, rev_a2, run_id)
    assert is_ready_rev is True
    assert "verdict" in resolved_rev
    assert resolved_rev["verdict"].attempt == 2
    assert "source_code" in resolved_rev
    assert resolved_rev["source_code"].attempt == 2
    assert "api_contract" in resolved_rev
    assert resolved_rev["api_contract"].attempt == 1

