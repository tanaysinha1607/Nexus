"""Phase 6b (part 1) Language-Generality unit tests."""

import json
import pytest
from app.models import Artifact, Node, NodeStatus
from orchestrator.agents.roles import ROLES
from orchestrator.config import HandlerConfig
from orchestrator.handlers.executor import handle_executor_node
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.sandbox.npm_audit_runner import parse_npm_audit_stdout
from orchestrator.sandbox.test_runner import parse_node_test_stdout


def test_architect_specifies_build_manifest_output():
    """Verify Architect role defines build_manifest output spec."""
    arch_role = ROLES["solution_architect"]
    kinds = [o.kind for o in arch_role.outputs]
    assert "architecture" in kinds
    assert "build_manifest" in kinds


def test_backend_and_qa_roles_accept_optional_build_manifest():
    """Verify Backend, QA, and Reviewer roles have optional build_manifest input selectors."""
    backend_role = ROLES["backend_engineer"]
    qa_role = ROLES["qa_engineer"]
    reviewer_role = ROLES["senior_reviewer"]

    b_kinds = [s.get("kind") for s in backend_role.input_selectors]
    q_kinds = [s.get("kind") for s in qa_role.input_selectors]
    r_kinds = [s.get("kind") for s in reviewer_role.input_selectors]

    assert "build_manifest" in b_kinds
    assert "build_manifest" in q_kinds
    assert "build_manifest" in r_kinds


def test_parse_npm_audit_stdout_clean_and_vulnerable():
    """Test npm audit JSON parsing for clean and vulnerable dependencies."""
    clean_json = json.dumps({
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 0,
                "critical": 0,
                "total": 0
            }
        }
    })
    completed, high, crit, mod, low, findings, ver = parse_npm_audit_stdout(clean_json)
    assert completed is True
    assert high == 0
    assert crit == 0
    assert len(findings) == 0

    # Teeth-test payload with HIGH vulnerability
    vulnerable_json = json.dumps({
        "auditReportVersion": 2,
        "vulnerabilities": {
            "express": {
                "name": "express",
                "severity": "high",
                "range": "<4.17.3",
                "title": "Open Redirect in Express"
            }
        },
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 1,
                "critical": 0,
                "total": 1
            }
        }
    })
    completed, high, crit, mod, low, findings, ver = parse_npm_audit_stdout(vulnerable_json)
    assert completed is True
    assert high == 1
    assert len(findings) == 1
    assert findings[0]["package"] == "express"


@pytest.mark.asyncio
async def test_security_validator_npm_audit_teeth_test():
    """Verify SecurityValidator passes clean npm audit report and rejects vulnerable report."""
    node = Node(name="SecurityValidator", agent_role="security_validator", config={})
    config = HandlerConfig()

    clean_report = {
        "scan_completed": True,
        "scanner": "npm_audit",
        "high_count": 0,
        "critical_count": 0,
        "high_findings": [],
    }
    clean_art = Artifact(kind="security_report", filename="security_report.json", content=json.dumps(clean_report))

    res_pass = await handle_validator_node(node, {"sec": clean_art}, config)
    assert res_pass.status == NodeStatus.completed
    assert res_pass.meta["passed"] is True

    # TEETH TEST: Vulnerable report MUST be rejected by SecurityValidator
    vulnerable_report = {
        "scan_completed": True,
        "scanner": "npm_audit",
        "high_count": 1,
        "critical_count": 0,
        "high_findings": [{
            "package": "express",
            "severity": "high",
            "title": "Known CVE vulnerability in express@4.16.0"
        }],
    }
    vuln_art = Artifact(kind="security_report", filename="security_report.json", content=json.dumps(vulnerable_report))

    res_fail = await handle_validator_node(node, {"sec": vuln_art}, config)
    assert res_fail.status == NodeStatus.completed
    assert res_fail.meta["passed"] is False
    assert len(res_fail.meta["failures"]) > 0
    assert "HIGH_DEPENDENCY_VULNERABILITY [express]" in res_fail.meta["failures"][0]


@pytest.mark.asyncio
async def test_manifest_missing_safety_runs_python_path():
    """Verify missing build_manifest artifact defaults to Python path safely without failing."""
    node = Node(name="SmokeExecutor", agent_role="executor", config={"mock_report": {
        "build_success": True,
        "container_started": True,
        "health_ok": True,
        "health_status_code": 200,
    }})
    config = HandlerConfig()

    source_art = Artifact(kind="source_code", filename="main.py", content="print('hello')")
    # Note: build_manifest artifact is ABSENT
    res = await handle_executor_node(node, {"source": source_art}, config)

    assert res.status == NodeStatus.completed
    assert len(res.artifacts) == 1
    assert res.artifacts[0].kind == "execution_report"


def test_parse_node_test_stdout():
    """Test parse_node_test_stdout for Node test runner outputs."""
    node_test_out = """
    ✔ GET /health (12ms)
    ✔ POST /api/v1/notes (45ms)
    ✔ GET /api/v1/notes (18ms)
    ℹ tests 3
    ℹ pass 3
    ℹ fail 0
    """
    col, passed, failed = parse_node_test_stdout(node_test_out)
    assert passed == 3
    assert failed == 0
    assert col == 3

    # Missing test setup handling
    no_tests_out = "npm ERR! Missing script: \"test\""
    col, passed, failed = parse_node_test_stdout(no_tests_out)
    assert failed >= 1
