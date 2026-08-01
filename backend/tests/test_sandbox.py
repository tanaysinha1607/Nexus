"""Unit tests for Phase 1.3b Executor and Validator logic."""

import json
import pytest
from app.models import Artifact, Node, NodeStatus, NodeType
from orchestrator.config import HandlerConfig
from orchestrator.handlers.executor import handle_executor_node
from orchestrator.handlers.validator import handle_validator_node


@pytest.mark.asyncio
async def test_validator_rule_passing_execution_report():
    report_content = json.dumps({
        "build_success": True,
        "build_logs_tail": "Successfully built",
        "container_started": True,
        "health_status_code": 200,
        "health_ok": True,
        "elapsed_s": 3.5,
        "container_logs_tail": "Application startup complete.",
    })
    art = Artifact(
        id=None, project_id=None, node_id=None, run_id=None,
        filename="execution_report.json", kind="execution_report",
        content=report_content, version=1, attempt=1
    )
    val_node = Node(name="Validator", node_type=NodeType.validator, agent_role="validator", config={})
    res = await handle_validator_node(val_node, {"execution_report": art}, HandlerConfig())

    assert res.status == NodeStatus.completed
    assert len(res.artifacts) == 1
    verdict = json.loads(res.artifacts[0].content)
    assert verdict["passed"] is True
    assert verdict["failures"] == []


@pytest.mark.asyncio
async def test_validator_rule_build_failed_report():
    report_content = json.dumps({
        "build_success": False,
        "build_logs_tail": "ERROR: Could not find package cryptography",
        "container_started": False,
        "health_status_code": None,
        "health_ok": False,
        "elapsed_s": 2.1,
        "container_logs_tail": "",
    })
    art = Artifact(
        id=None, project_id=None, node_id=None, run_id=None,
        filename="execution_report.json", kind="execution_report",
        content=report_content, version=1, attempt=1
    )
    val_node = Node(name="Validator", node_type=NodeType.validator, agent_role="validator", config={})
    res = await handle_validator_node(val_node, {"execution_report": art}, HandlerConfig())

    assert res.status == NodeStatus.completed
    assert len(res.artifacts) == 1
    verdict = json.loads(res.artifacts[0].content)
    assert verdict["passed"] is False
    assert "build_failed" in verdict["failures"]


@pytest.mark.asyncio
async def test_validator_rule_health_failed_report():
    report_content = json.dumps({
        "build_success": True,
        "build_logs_tail": "Build success",
        "container_started": True,
        "health_status_code": 500,
        "health_ok": False,
        "elapsed_s": 8.0,
        "container_logs_tail": "ModuleNotFoundError: No module named 'passlib'",
    })
    art = Artifact(
        id=None, project_id=None, node_id=None, run_id=None,
        filename="execution_report.json", kind="execution_report",
        content=report_content, version=1, attempt=1
    )
    val_node = Node(name="Validator", node_type=NodeType.validator, agent_role="validator", config={})
    res = await handle_validator_node(val_node, {"execution_report": art}, HandlerConfig())

    assert res.status == NodeStatus.completed
    assert len(res.artifacts) == 1
    verdict = json.loads(res.artifacts[0].content)
    assert verdict["passed"] is False
    assert any("health_check_failed" in f for f in verdict["failures"])


@pytest.mark.asyncio
async def test_executor_mock_report_generation():
    mock_rep = {
        "build_success": True,
        "build_logs_tail": "Mock build success",
        "container_started": True,
        "health_status_code": 200,
        "health_ok": True,
        "elapsed_s": 1.0,
        "container_logs_tail": "Mock logs",
    }
    exec_node = Node(name="BackendExecutor", node_type=NodeType.executor, agent_role="backend_executor", config={"mock_report": mock_rep})
    art = Artifact(
        id=None, project_id=None, node_id=None, run_id=None,
        filename="main.py", kind="source_code",
        content="from fastapi import FastAPI\napp = FastAPI()", version=1, attempt=1
    )
    res = await handle_executor_node(exec_node, {"main.py": art}, HandlerConfig())

    assert res.status == NodeStatus.completed
    assert len(res.artifacts) == 1
    report = json.loads(res.artifacts[0].content)
    assert report["build_success"] is True
    assert report["health_ok"] is True
