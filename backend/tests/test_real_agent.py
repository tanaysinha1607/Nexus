"""Unit and live integration tests for the real agent handler, output parser, and PM agent role."""

import os
import uuid
import pytest
from sqlalchemy import select

from app.models import Artifact, Node, NodeStatus, NodeType, Project, Run, RunStatus
from orchestrator import (
    HandlerConfig,
    RunScheduler,
    SchedulerConfig,
)
from orchestrator.agents.parsing import parse_agent_output
from orchestrator.agents.roles import OutputSpec, ROLES
from orchestrator.handlers import ArtifactSpec
from orchestrator.handlers.real_agent import assemble_user_message, handle_real_agent_node
from orchestrator.llm import AnthropicLLMClient, FakeLLMClient, LLMError


# ---------------------------------------------------------------------------
# 1. Output Parser Unit Tests (5 Cases)
# ---------------------------------------------------------------------------
def test_parser_happy_path():
    text = """=== FILE: prd.md ===
```markdown
# Title
PRD Content
```"""
    specs = [OutputSpec(kind="prd", filename="prd.md", required=True)]
    is_valid, artifacts, log_reason = parse_agent_output(text, specs)
    assert is_valid
    assert len(artifacts) == 1
    assert artifacts[0].kind == "prd"
    assert artifacts[0].filename == "prd.md"
    assert artifacts[0].content == "# Title\nPRD Content"


def test_parser_missing_required_file():
    text = "=== FILE: other.txt ===\nSome content"
    specs = [OutputSpec(kind="prd", filename="prd.md", required=True)]
    is_valid, artifacts, log_reason = parse_agent_output(text, specs)
    assert not is_valid
    assert len(artifacts) == 1
    assert artifacts[0].kind == "raw_response"
    assert "missing required file" in log_reason.lower()


def test_parser_extra_unknown_file_ignored():
    text = """=== FILE: prd.md ===
```markdown
PRD Content
```
=== FILE: random_notes.txt ===
Ignored text"""
    specs = [OutputSpec(kind="prd", filename="prd.md", required=True)]
    is_valid, artifacts, log_reason = parse_agent_output(text, specs)
    assert is_valid
    assert len(artifacts) == 1
    assert artifacts[0].filename == "prd.md"


def test_parser_no_fences_at_all():
    text = "Plain unformatted response with no file headers"
    specs = [OutputSpec(kind="prd", filename="prd.md", required=True)]
    is_valid, artifacts, log_reason = parse_agent_output(text, specs)
    assert not is_valid
    assert len(artifacts) == 1
    assert artifacts[0].kind == "raw_response"
    assert artifacts[0].content == text


def test_parser_nested_backticks_inside_content():
    text = """=== FILE: prd.md ===
```markdown
# Title
```python
def example():
    return 42
```
End of PRD
```"""
    specs = [OutputSpec(kind="prd", filename="prd.md", required=True)]
    is_valid, artifacts, log_reason = parse_agent_output(text, specs)
    assert is_valid
    assert len(artifacts) == 1
    assert "def example():" in artifacts[0].content


# ---------------------------------------------------------------------------
# 2. Handler Prompt Assembly & Unit Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_assembly_and_fake_llm_calls():
    fake_llm = FakeLLMClient(
        canned_responses={
            "product_manager": "=== FILE: prd.md ===\n```markdown\n# Generated PRD\n```"
        }
    )
    node = Node(
        id=uuid.uuid4(),
        name="PM",
        node_type=NodeType.agent,
        agent_role="product_manager",
    )
    user_prompt_art = Artifact(
        id=uuid.uuid4(),
        filename="user_prompt.txt",
        kind="user_prompt",
        produced_by_role="system",
        version=1,
        content="Build a paper trading app",
    )
    inputs = {"user_prompt": user_prompt_art}
    cfg = HandlerConfig()

    res = await handle_real_agent_node(node, inputs, cfg, llm_client=fake_llm)
    assert res.status == NodeStatus.completed
    assert len(fake_llm.calls) == 1

    prompt_content = fake_llm.calls[0]["messages"][0]["content"]
    assert "## INPUT: user_prompt (from system, v1)" in prompt_content
    assert "Build a paper trading app" in prompt_content


@pytest.mark.asyncio
async def test_pm_node_happy_path_emits_prd_and_prompt(session_factory, test_project):
    fake_llm = FakeLLMClient(
        canned_responses={
            "product_manager": "=== FILE: prd.md ===\n```markdown\n# Complete PRD\nFeature list here.\n```"
        }
    )
    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            id=uuid.uuid4(),
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content="Build a paper trading dashboard",
            version=1,
            attempt=1,
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        db.add_all([seed_art, pm_node])
        await db.commit()
        run_id = run.id

    scheduler_cfg = SchedulerConfig(use_real_agents=True, lease_seconds=1.0, poll_interval=0.05)
    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=scheduler_cfg,
        handler_config=HandlerConfig(),
        llm_client=fake_llm,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        res = await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.node_id == pm_node.id))
        arts = list(res.scalars().all())
        art_kinds = {a.kind for a in arts}
        assert "prd" in art_kinds
        assert "prompt" in art_kinds


@pytest.mark.asyncio
async def test_llm_error_node_failed(session_factory, test_project, monkeypatch):
    class ErrorLLM(FakeLLMClient):
        async def complete(self, *args, **kwargs):
            raise LLMError("API Connection Refused")

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Build app",
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        db.add_all([seed_art, pm_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=ErrorLLM(),
    )
    final_status = await scheduler.run()
    assert final_status == RunStatus.failed

    async with session_factory() as db:
        pm = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "PM"))).scalar_one()
        assert pm.status == NodeStatus.failed


@pytest.mark.asyncio
async def test_unparseable_response_emits_raw_response_artifact(session_factory, test_project):
    unparseable_llm = FakeLLMClient(
        canned_responses={"product_manager": "No file headers present here"},
        default_response="No file headers present here",
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Build app",
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        db.add_all([seed_art, pm_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=unparseable_llm,
    )
    final_status = await scheduler.run()
    assert final_status == RunStatus.failed

    async with session_factory() as db:
        pm = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "PM"))).scalar_one()
        assert pm.status == NodeStatus.failed

        raw_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "raw_response"))).scalar_one_or_none()
        assert raw_art is not None
        assert raw_art.content == "No file headers present here"


@pytest.mark.asyncio
async def test_truncated_max_tokens_causes_failure(monkeypatch):
    class TruncatedLLM(FakeLLMClient):
        async def complete(self, *args, **kwargs):
            res = await super().complete(*args, **kwargs)
            res.stop_reason = "max_tokens"
            return res

    fake_llm = TruncatedLLM(
        canned_responses={
            "product_manager": "=== FILE: prd.md ===\n```markdown\n# Truncated PRD\n```"
        }
    )
    node = Node(name="PM", node_type=NodeType.agent, agent_role="product_manager")
    art = Artifact(filename="user_prompt.txt", kind="user_prompt", content="prompt")
    inputs = {"user_prompt": art}

    res = await handle_real_agent_node(node, inputs, HandlerConfig(), llm_client=fake_llm)
    assert res.status == NodeStatus.failed
    assert "truncated" in res.logs.lower()


@pytest.mark.asyncio
async def test_use_real_agents_false_never_invokes_real_handler(session_factory, test_project, monkeypatch):
    invoked = False

    async def tracking_real_handler(*args, **kwargs):
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(
        "orchestrator.handlers.real_agent.handle_real_agent_node", tracking_real_handler
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            content="Build app",
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        db.add_all([seed_art, pm_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=False),  # Real agents FALSE
        handler_config=HandlerConfig(),
    )
    await scheduler.run()

    assert not invoked


@pytest.mark.asyncio
async def test_architect_prompt_assembly_and_valid_contract():
    fake_llm = FakeLLMClient()
    arch_node = Node(
        id=uuid.uuid4(),
        name="Architect",
        node_type=NodeType.agent,
        agent_role="solution_architect",
    )
    prd_art = Artifact(
        id=uuid.uuid4(),
        filename="prd.md",
        kind="prd",
        produced_by_role="product_manager",
        version=1,
        content="# Sample PRD Content for Cryptopaper",
    )
    inputs = {"prd": prd_art}
    cfg = HandlerConfig()

    res = await handle_real_agent_node(arch_node, inputs, cfg, llm_client=fake_llm)
    assert res.status == NodeStatus.completed
    assert len(fake_llm.calls) == 1

    prompt_content = fake_llm.calls[0]["messages"][0]["content"]
    assert "## INPUT: prd (from product_manager, v1)" in prompt_content
    assert "Sample PRD Content for Cryptopaper" in prompt_content

    kinds = {a.kind for a in res.artifacts}
    assert "architecture" in kinds


@pytest.mark.asyncio
async def test_api_designer_invalid_json_fails_and_emits_raw_response():
    invalid_json_llm = FakeLLMClient(
        canned_responses={
            "api_designer": (
                "{ NOT VALID JSON }"
            )
        }
    )
    api_node = Node(
        id=uuid.uuid4(),
        name="ApiDesigner",
        node_type=NodeType.agent,
        agent_role="api_designer",
    )
    arch_art = Artifact(filename="architecture.md", kind="architecture", content="# Arch text")
    inputs = {"architecture": arch_art}

    res = await handle_real_agent_node(api_node, inputs, HandlerConfig(), llm_client=invalid_json_llm)
    assert res.status == NodeStatus.failed

    kinds = {a.kind for a in res.artifacts}
    assert "raw_response" in kinds
    assert "api_contract" not in kinds


@pytest.mark.asyncio
async def test_full_pm_arch_chain(session_factory, test_project):
    fake_llm = FakeLLMClient()

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            id=uuid.uuid4(),
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content="Build paper trading platform",
            version=1,
            attempt=1,
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        db.add_all([seed_art, pm_node, arch_node, api_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=fake_llm,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        res_nodes = await db.execute(select(Node).where(Node.run_id == run_id))
        nodes = list(res_nodes.scalars().all())
        assert len(nodes) == 3
        assert all(n.status == NodeStatus.completed for n in nodes)

        res_arts = await db.execute(select(Artifact).where(Artifact.run_id == run_id))
        artifacts = list(res_arts.scalars().all())
        art_kinds = {a.kind for a in artifacts}

        assert "user_prompt" in art_kinds
        assert "prd" in art_kinds
        assert "prompt" in art_kinds
        assert "architecture" in art_kinds
        assert "api_contract" in art_kinds
        # 1 seed + 2 PM artifacts + 2 Architect artifacts + 2 ApiDesigner artifacts = 7 total artifacts
        assert len(artifacts) == 7


# ---------------------------------------------------------------------------
# 3. Live Integration Test (@pytest.mark.live)
# ---------------------------------------------------------------------------
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_pm_agent_execution(session_factory, test_project):
    """Executes the PM agent node against the configured LLM API (Gemini or Anthropic) with canonical paper trading prompt."""
    from orchestrator.llm.factory import get_default_llm_client
    from orchestrator.llm.llm_client import FakeLLMClient

    llm_client = get_default_llm_client()
    if isinstance(llm_client, FakeLLMClient):
        pytest.skip("Neither GEMINI_API_KEY nor ANTHROPIC_API_KEY is set")

    canonical_prompt = (
        "Build a cryptocurrency paper trading platform with authentication, "
        "a dashboard, charts, portfolio management, and an admin panel."
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            id=uuid.uuid4(),
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content=canonical_prompt,
            version=1,
            attempt=1,
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        db.add_all([seed_art, pm_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=llm_client,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        pm = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "PM"))).scalar_one()
        assert pm.status == NodeStatus.completed

        prd_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "prd"))).scalar_one()
        prompt_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "prompt"))).scalar_one()

        assert len(prd_art.content) > 100
        assert canonical_prompt in prompt_art.content

        print("\n" + "=" * 80)
        print("GENERATED PRD ARTIFACT (prd.md):")
        print("=" * 80)
        print(prd_art.content)
        print("=" * 80 + "\n")


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_pm_arch_execution(session_factory, test_project):
    """Executes the PM -> Architect agent chain against the configured LLM API (Gemini/Anthropic)."""
    import json
    from orchestrator.llm.factory import get_default_llm_client
    from orchestrator.llm.llm_client import FakeLLMClient

    llm_client = get_default_llm_client()
    if isinstance(llm_client, FakeLLMClient):
        pytest.skip("Neither GEMINI_API_KEY nor ANTHROPIC_API_KEY is set")

    canonical_prompt = (
        "Build a cryptocurrency paper trading platform with authentication, "
        "a dashboard, charts, portfolio management, and an admin panel."
    )

    async with session_factory() as db:
        run = Run(project_id=test_project.id)
        db.add(run)
        await db.flush()

        seed_art = Artifact(
            id=uuid.uuid4(),
            project_id=test_project.id,
            node_id=None,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content=canonical_prompt,
            version=1,
            attempt=1,
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        db.add_all([seed_art, pm_node, arch_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=llm_client,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        arch = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "Architect"))).scalar_one()
        assert arch.status == NodeStatus.completed

        arch_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "architecture"))).scalar_one()
        contract_art = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "api_contract"))).scalar_one()
        arch_prompt = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.node_id == arch.id, Artifact.kind == "prompt"))).scalar_one()

        # Validate api_contract.json
        contract_json = json.loads(contract_art.content)
        assert "endpoints" in contract_json
        assert len(contract_json["endpoints"]) >= 3

        print("\n" + "=" * 80)
        print("GENERATED ARCHITECTURE ARTIFACT (architecture.md):")
        print("=" * 80)
        print(arch_art.content)
        print("=" * 80 + "\n")

        print("\n" + "=" * 80)
        print("GENERATED API CONTRACT ARTIFACT (api_contract.json):")
        print("=" * 80)
        print(contract_art.content)
        print("=" * 80 + "\n")

        print("\n" + "=" * 80)
        print("ARCHITECT PROMPT ARTIFACT (prompt_Architect.md):")
        print("=" * 80)
        print(arch_prompt.content)
        print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# 6. Backend Engineer Agent Node & Parsing Unit Tests
# ---------------------------------------------------------------------------
def test_backend_engineer_emits_multiple_source_code_artifacts():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
```

=== FILE: requirements.txt ===
```text
fastapi==0.115.0
uvicorn==0.30.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert is_valid
    assert len(artifacts) == 2
    assert all(a.kind == "source_code" for a in artifacts)
    filenames = [a.filename for a in artifacts]
    assert "main.py" in filenames
    assert "requirements.txt" in filenames


def test_missing_main_py_fails_node():
    text = """=== FILE: app.py ===
```python
print('hello')
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert log_reason == "missing entrypoint main.py"


def test_syntax_error_in_main_py_fails_node():
    text = """=== FILE: main.py ===
```python
def invalid_python_syntax(
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert "syntax error" in log_reason


def test_missing_or_empty_requirements_txt_fails_node():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
app = FastAPI()
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert log_reason == "missing or empty requirements.txt"


def test_forbidden_external_dependency_fails_node():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
import sqlalchemy
from redis import Redis

app = FastAPI()
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
sqlalchemy==2.0.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert "depends on external services" in log_reason
    assert "redis" in log_reason
    assert "sqlalchemy" in log_reason


def test_accept_any_file_only_applies_to_backend_engineer():
    pm_role = ROLES["product_manager"]
    backend_role = ROLES["backend_engineer"]
    assert not getattr(pm_role, "accept_any_file", False)
    assert getattr(backend_role, "accept_any_file", False)


@pytest.mark.asyncio
async def test_full_pm_arch_backend_chain(session_factory):
    llm_client = FakeLLMClient()

    async with session_factory() as db:
        test_project = Project(name="PM Arch Backend Test Project", user_prompt="Build a crypto trading platform MVP.")
        db.add(test_project)
        await db.commit()

        run = Run(project_id=test_project.id)
        db.add(run)
        await db.commit()

        seed_art = Artifact(
            project_id=test_project.id,
            run_id=run.id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content=test_project.user_prompt,
        )
        pm_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="PM",
            node_type=NodeType.agent,
            agent_role="product_manager",
            config={"required_inputs": [{"kind": "user_prompt"}]},
        )
        arch_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Architect",
            node_type=NodeType.agent,
            agent_role="solution_architect",
            config={"required_inputs": [{"kind": "prd"}]},
        )
        api_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="ApiDesigner",
            node_type=NodeType.agent,
            agent_role="api_designer",
            config={"required_inputs": [{"kind": "architecture"}]},
        )
        backend_node = Node(
            project_id=test_project.id,
            run_id=run.id,
            name="Backend",
            node_type=NodeType.agent,
            agent_role="backend_engineer",
            config={"required_inputs": [{"kind": "api_contract"}]},
        )
        db.add_all([seed_art, pm_node, arch_node, api_node, backend_node])
        await db.commit()
        run_id = run.id

    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=SchedulerConfig(use_real_agents=True),
        handler_config=HandlerConfig(),
        llm_client=llm_client,
    )

    final_status = await scheduler.run()
    assert final_status == RunStatus.completed

    async with session_factory() as db:
        b_node = (await db.execute(select(Node).where(Node.run_id == run_id, Node.name == "Backend"))).scalar_one()
        assert b_node.status == NodeStatus.completed

        b_arts = (await db.execute(select(Artifact).where(Artifact.run_id == run_id, Artifact.produced_by_role == "backend_engineer"))).scalars().all()
        assert len(b_arts) >= 2
        filenames = [a.filename for a in b_arts]
        assert "main.py" in filenames
        assert "requirements.txt" in filenames


def test_missing_cryptography_in_requirements_fails_node():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
from cryptography.hazmat.primitives import serialization

app = FastAPI()
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
uvicorn==0.30.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert "requirements.txt missing packages for imports: cryptography" in log_reason


def test_missing_email_validator_for_emailstr_fails_node():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
from pydantic import BaseModel, EmailStr

app = FastAPI()

class User(BaseModel):
    email: EmailStr
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
uvicorn==0.30.0
pydantic==2.8.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert not is_valid
    assert artifacts[0].kind == "raw_response"
    assert "requirements.txt missing packages for imports: email-validator" in log_reason


def test_happy_path_all_imported_packages_in_requirements_passes():
    text = """=== FILE: main.py ===
```python
from fastapi import FastAPI
from pydantic import BaseModel, EmailStr
from cryptography.hazmat.primitives import serialization
from jose import jwt

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
```
=== FILE: requirements.txt ===
```text
fastapi==0.115.0
uvicorn==0.30.0
pydantic==2.8.0
email-validator==2.1.0
cryptography==42.0.0
python-jose[cryptography]==3.3.0
```"""
    role = ROLES["backend_engineer"]
    is_valid, artifacts, log_reason = parse_agent_output(text, role.outputs, role=role)
    assert is_valid
    assert len(artifacts) == 2
    assert log_reason == "agent output successfully parsed"


def test_rework_prompt_truncation_preserves_failure_context_and_contract():
    """Verify that when context exceeds max_input_chars, failure_context and api_contract are NEVER truncated."""
    role = ROLES["backend_engineer"]
    traceback_str = "ImportError: email-validator version >= 2.0 required for EmailStr validation"
    
    contract_art = Artifact(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
        filename="api_contract.json", kind="api_contract", produced_by_role="api_designer",
        content="{" + '"endpoint": "schema_data"' * 100 + "}", version=1, attempt=1
    )
    fail_art = Artifact(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
        filename="failure_context.json", kind="failure_context", produced_by_role="validator",
        content="{" + f'"traceback": "{traceback_str}"' + "}", version=1, attempt=2
    )
    code_art = Artifact(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
        filename="main.py", kind="source_code", produced_by_role="backend_engineer",
        content="# " + ("def huge_function(): pass\n" * 800), version=1, attempt=1
    )

    inputs = {
        "api_contract": contract_art,
        "failure_context": fail_art,
        "main.py": code_art,
    }

    assembled = assemble_user_message(inputs, role)

    # 1. Assert total length does not exceed role.max_input_chars
    assert len(assembled) <= role.max_input_chars

    # 2. Assert failure_context traceback is 100% intact (NEVER TRUNCATED)
    assert traceback_str in assembled

    # 3. Assert api_contract content is 100% intact (NEVER TRUNCATED)
    assert "schema_data" in assembled

    # 4. Assert source_code was middle-out truncated
    assert "[... TRUNCATED" in assembled




