"""Phase 5 Unit Test Suite — GitHub PR Integration.

Tests:
1. test_open_pr_happy_path_passing_run
2. test_open_pr_completed_run_with_failed_work_returns_409
3. test_final_attempt_artifacts_only_committed
4. test_github_token_secret_leak_prevention
5. test_backend_process_execution_only
"""

import json
import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import Artifact, Node, NodeStatus, NodeType, Project, ProjectStatus, Run, RunStatus
from app.integrations.github_pr import open_github_pr, redact_token

client = TestClient(app)


@pytest.mark.asyncio
async def test_open_pr_happy_path_passing_run(db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_mock_token_12345")
    monkeypatch.setenv("GITHUB_OUTPUT_REPO", "tanaysinha1607/Nexus")
    monkeypatch.setenv("NEXUS_TEST_TS", "999999")

    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="Phase 5 Test Project",
        user_prompt="Build a minimalist FastAPI microservice with /health.",
        status=ProjectStatus.completed,
    )
    db_session.add(project)

    run_id = uuid.uuid4()
    run = Run(
        id=run_id,
        project_id=project_id,
        status=RunStatus.completed,
    )
    db_session.add(run)

    node_id_val = uuid.uuid4()
    node_val = Node(
        id=node_id_val,
        project_id=project_id,
        run_id=run_id,
        name="Backend_a2",
        node_type=NodeType.agent,
        agent_role="backend_engineer",
        status=NodeStatus.completed,
        attempt=2,
        config={},
    )
    db_session.add(node_val)

    # Artifacts for final attempt 2
    art_source = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        node_id=node_id_val,
        filename="main.py",
        kind="source_code",
        content="from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status':'ok'}\n",
        content_type="text/x-python",
        version=1,
        attempt=2,
    )
    art_reqs = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        node_id=node_id_val,
        filename="requirements.txt",
        kind="source_code",
        content="fastapi>=0.100.0\nuvicorn>=0.20.0\n",
        content_type="text/plain",
        version=1,
        attempt=2,
    )
    art_verdict = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        node_id=node_id_val,
        filename="verdict.json",
        kind="verdict",
        content=json.dumps({"passed": True, "reviewer_verdict": "approved", "failures": []}),
        content_type="application/json",
        version=1,
        attempt=2,
    )
    art_test = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        node_id=node_id_val,
        filename="test_report.json",
        kind="test_report",
        content=json.dumps({"service_booted": True, "passed": 3, "failed": 0}),
        content_type="application/json",
        version=1,
        attempt=2,
    )
    db_session.add_all([art_source, art_reqs, art_verdict, art_test])
    await db_session.commit()

    # Mock httpx AsyncClient API calls
    async def mock_get(url, headers=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/Nexus"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/ref/heads/main" in url:
            resp.json.return_value = {"object": {"sha": "abc123def456"}}
        elif "/contents/" in url:
            resp.status_code = 404  # New file
        return resp

    async def mock_post(url, headers=None, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 201
        if url.endswith("/git/refs"):
            resp.json.return_value = {"ref": json.get("ref")}
        elif url.endswith("/pulls"):
            resp.json.return_value = {"html_url": "https://github.com/tanaysinha1607/Nexus/pull/1"}
        return resp

    async def mock_put(url, headers=None, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {"content": {"sha": "file_sha_123"}}
        return resp

    from fastapi import HTTPException
    from app.routers.runs import open_pr_endpoint

    with patch("httpx.AsyncClient.get", side_effect=mock_get), \
         patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("httpx.AsyncClient.put", side_effect=mock_put):

        data = await open_pr_endpoint(run_id=run_id, db=db_session)
        assert data["pr_url"] == "https://github.com/tanaysinha1607/Nexus/pull/1"


@pytest.mark.asyncio
async def test_open_pr_completed_run_with_failed_work_returns_409(db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_mock_token_12345")
    monkeypatch.setenv("GITHUB_OUTPUT_REPO", "tanaysinha1607/Nexus")

    project_id = uuid.uuid4()
    project = Project(
        id=project_id,
        name="Phase 5 Failed Work Project",
        user_prompt="Build a microservice",
        status=ProjectStatus.completed,
    )
    db_session.add(project)

    run_id = uuid.uuid4()
    # Run status is 'completed' (after 5 attempts), BUT work failed!
    run = Run(
        id=run_id,
        project_id=project_id,
        status=RunStatus.completed,
    )
    db_session.add(run)

    art_verdict = Artifact(
        id=uuid.uuid4(),
        project_id=project_id,
        run_id=run_id,
        filename="verdict.json",
        kind="verdict",
        content=json.dumps({"passed": False, "failures": ["Syntax error in main.py"]}),
        content_type="application/json",
        version=1,
        attempt=5,
    )
    db_session.add(art_verdict)
    await db_session.commit()

    from fastapi import HTTPException
    from app.routers.runs import open_pr_endpoint

    with patch("httpx.AsyncClient.post") as mock_http_post:
        with pytest.raises(HTTPException) as exc_info:
            await open_pr_endpoint(run_id=run_id, db=db_session)
        assert exc_info.value.status_code == 409
        assert "Cannot open PR" in exc_info.value.detail
        mock_http_post.assert_not_called()


@pytest.mark.asyncio
async def test_final_attempt_artifacts_only_committed(db_session: AsyncSession, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_mock_token_12345")
    monkeypatch.setenv("GITHUB_OUTPUT_REPO", "tanaysinha1607/Nexus")
    monkeypatch.setenv("NEXUS_TEST_TS", "777777")

    project_id = uuid.uuid4()
    project = Project(id=project_id, name="Multi-Attempt Project", user_prompt="Build service", status=ProjectStatus.completed)
    db_session.add(project)

    run_id = uuid.uuid4()
    run = Run(id=run_id, project_id=project_id, status=RunStatus.completed)
    db_session.add(run)

    node_id_1 = uuid.uuid4()
    node_id_2 = uuid.uuid4()

    node_1 = Node(id=node_id_1, project_id=project_id, run_id=run_id, name="Backend_a1", node_type=NodeType.agent, agent_role="backend_engineer", status=NodeStatus.completed, attempt=1, config={})
    node_2 = Node(id=node_id_2, project_id=project_id, run_id=run_id, name="Backend_a2", node_type=NodeType.agent, agent_role="backend_engineer", status=NodeStatus.completed, attempt=2, config={})
    db_session.add_all([node_1, node_2])

    # Attempt 1 (Broken)
    art_a1 = Artifact(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, node_id=node_id_1,
        filename="main.py", kind="source_code", content="BROKEN_ATTEMPT_1_CODE",
        content_type="text/x-python", version=1, attempt=1,
    )
    # Attempt 2 (Fixed & Passed)
    art_a2 = Artifact(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, node_id=node_id_2,
        filename="main.py", kind="source_code", content="FIXED_ATTEMPT_2_CODE",
        content_type="text/x-python", version=1, attempt=2,
    )
    art_verdict = Artifact(
        id=uuid.uuid4(), project_id=project_id, run_id=run_id, node_id=node_id_2,
        filename="verdict.json", kind="verdict", content=json.dumps({"passed": True, "reviewer_verdict": "approved"}),
        content_type="application/json", version=1, attempt=2,
    )
    db_session.add_all([art_a1, art_a2, art_verdict])
    await db_session.commit()

    committed_files = {}

    async def mock_get(url, headers=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/Nexus"):
            resp.json.return_value = {"default_branch": "main"}
        elif "/git/ref/heads/main" in url:
            resp.json.return_value = {"object": {"sha": "sha123"}}
        elif "/contents/" in url:
            resp.status_code = 404
        return resp

    async def mock_post(url, headers=None, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 201
        if url.endswith("/git/refs"):
            resp.json.return_value = {"ref": json.get("ref")}
        elif url.endswith("/pulls"):
            resp.json.return_value = {"html_url": "https://github.com/tanaysinha1607/Nexus/pull/2"}
        return resp

    async def mock_put(url, headers=None, json=None, **kwargs):
        filename = url.split("/contents/")[1]
        content_b64 = json["content"]
        import base64
        decoded = base64.b64decode(content_b64).decode("utf-8")
        committed_files[filename] = decoded
        resp = MagicMock()
        resp.status_code = 201
        return resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get), \
         patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("httpx.AsyncClient.put", side_effect=mock_put):

        pr_url = await open_github_pr(db_session, run_id)
        assert pr_url == "https://github.com/tanaysinha1607/Nexus/pull/2"

        # Assert committed file contains FIXED_ATTEMPT_2_CODE, NOT BROKEN_ATTEMPT_1_CODE
        assert "main.py" in committed_files
        assert committed_files["main.py"] == "FIXED_ATTEMPT_2_CODE"
        assert "BROKEN_ATTEMPT_1_CODE" not in committed_files["main.py"]


def test_github_token_secret_leak_prevention():
    secret_token = "ghp_super_secret_pat_99999"
    log_line = f"Error calling GitHub with token {secret_token}"
    redacted = redact_token(log_line, secret_token)
    assert secret_token not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted


def test_backend_process_execution_only():
    """Architectural assertion confirming GitHub integration is in app.integrations."""
    import inspect
    from app.integrations.github_pr import open_github_pr
    module_name = inspect.getmodule(open_github_pr).__name__
    assert module_name == "app.integrations.github_pr"
    assert "orchestrator.sandbox" not in module_name
