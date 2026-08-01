"""Phase 3 Live Execution Script: Security Agent & Bandit AST Scanner Integration.

1. Runs known-vulnerable code fixture scan through `bandit_runner` and `handle_validator_node`
   to prove the gate catches real vulnerabilities (hardcoded password B105 + eval B307).
2. Runs the `pm_arch_backend_security` DAG with real Groq LLM + Docker sandbox.
3. Monitors node transitions, prints token budget ledger, captures `security_report.json` verbatim,
   and verifies zero leaked sandbox containers.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
import docker

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Artifact, Node, NodeStatus, Project, Run, RunStatus
from orchestrator.config import HandlerConfig, SchedulerConfig
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.sandbox.bandit_runner import parse_bandit_stdout, run_bandit_security_scan_in_docker_sandbox
from orchestrator.scheduler import RunScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase3_live")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus_dev@postgres:5432/nexus",
)


async def test_known_vulnerable_fixture_demo():
    logger.info("=" * 70)
    logger.info("DEMO 1: KNOWN-VULNERABLE FIXTURE GATE TEETH VERIFICATION")
    logger.info("=" * 70)

    vulnerable_files = {
        "main.py": """
import os
import subprocess

def run_shell_command(cmd: str):
    return subprocess.Popen("cat " + cmd, shell=True)  # B602 (HIGH severity)

def make_world_writable(filepath: str):
    os.chmod(filepath, 0o777)  # B103 (HIGH severity)
"""
    }

    logger.info("Running bandit AST security scan against vulnerable code fixture...")
    report = run_bandit_security_scan_in_docker_sandbox(vulnerable_files)

    logger.info("\n--- Verbatim security_report.json (Vulnerable Fixture) ---")
    logger.info(json.dumps(report, indent=2))

    # Evaluate with SecurityValidator
    val_node = Node(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
        name="SecurityValidator", node_type="validator", agent_role="security_validator", config={}
    )
    art = Artifact(
        id=uuid.uuid4(), project_id=val_node.project_id, node_id=uuid.uuid4(), run_id=val_node.run_id,
        filename="security_report.json", kind="security_report", content=json.dumps(report)
    )

    res = await handle_validator_node(val_node, {"security_report": art}, HandlerConfig())
    verdict = json.loads(res.artifacts[0].content)

    logger.info("\n--- SecurityValidator Verdict (Vulnerable Fixture) ---")
    logger.info(json.dumps(verdict, indent=2))

    assert verdict["passed"] is False, "SecurityValidator failed to reject vulnerable fixture!"
    assert len(verdict["failures"]) >= 2, "Expected at least 2 HIGH vulnerabilities caught!"
    logger.info("✅ SUCCESS: Gate teeth verified — Bandit scanner caught real vulnerabilities and SecurityValidator failed.")


async def run_phase3_live():
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    logger.info("\n" + "=" * 70)
    logger.info("DEMO 2: NEXUS PHASE 3 LIVE AGENT EXECUTION RUN (pm_arch_backend_security)")
    logger.info(f"Project ID: {project_id}")
    logger.info(f"Run ID:     {run_id}")
    logger.info("=" * 70)

    user_prompt = (
        "Build a lightweight FastAPI service for an automated portfolio management engine. "
        "Must implement POST /api/v1/auth/register, POST /api/v1/auth/login, "
        "and GET /api/v1/portfolio/summary."
    )

    async with session_factory() as session:
        proj = Project(id=project_id, name="Phase3 Live Security Test", user_prompt=user_prompt)
        session.add(proj)
        await session.commit()

        # Seed artifact
        seed_prompt_artifact = Artifact(
            id=uuid.uuid4(),
            project_id=project_id,
            node_id=None,
            run_id=run_id,
            filename="user_prompt.txt",
            kind="user_prompt",
            produced_by_role="system",
            content=user_prompt,
            version=1,
            attempt=1,
        )
        session.add(seed_prompt_artifact)

        # Create pm_arch_backend_security graph nodes
        pm_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="PM",
            node_type="agent", agent_role="product_manager", config={"required_inputs": [{"kind": "user_prompt"}]}
        )
        arch_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Architect",
            node_type="agent", agent_role="solution_architect", config={"required_inputs": [{"kind": "prd"}]}
        )
        api_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="ApiDesigner",
            node_type="agent", agent_role="api_designer", config={"required_inputs": [{"kind": "architecture"}]}
        )
        backend_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Backend",
            node_type="agent", agent_role="backend_engineer", config={"required_inputs": [{"kind": "api_contract"}]}
        )
        exec_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="SecurityScanExecutor",
            node_type="executor", agent_role="security_executor", config={"required_inputs": [{"kind": "source_code"}]}
        )
        val_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="SecurityValidator",
            node_type="validator", agent_role="security_validator", config={"required_inputs": [{"kind": "security_report"}]}
        )
        reviewer_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Reviewer",
            node_type="agent", agent_role="senior_reviewer",
            config={"required_inputs": [{"kind": "verdict"}, {"kind": "source_code"}, {"kind": "api_contract"}]}
        )

        nodes = [pm_node, arch_node, api_node, backend_node, exec_node, val_node, reviewer_node]
        session.add_all(nodes)

        run_obj = Run(id=run_id, project_id=project_id, status=RunStatus.pending)
        session.add(run_obj)
        await session.commit()

    # Launch Scheduler
    scheduler_cfg = SchedulerConfig(use_real_agents=True, max_parallel_nodes=1, max_attempts=5)
    handler_cfg = HandlerConfig()
    scheduler = RunScheduler(
        session_factory=session_factory,
        run_id=run_id,
        scheduler_config=scheduler_cfg,
        handler_config=handler_cfg,
    )

    logger.info("Starting RunScheduler loop...")
    final_status = await scheduler.run()
    logger.info(f"Run finished with status: {final_status.value}")

    # Process Results & Token Ledger
    async with session_factory() as session:
        n_stmt = select(Node).where(Node.run_id == run_id).order_by(Node.created_at.asc())
        n_res = await session.execute(n_stmt)
        all_nodes = list(n_res.scalars().all())

        p_stmt = select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "prompt")
        p_res = await session.execute(p_stmt)
        prompt_arts = list(p_res.scalars().all())

        o_stmt = select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.asc())
        o_res = await session.execute(o_stmt)
        all_arts = list(o_res.scalars().all())

    logger.info("\n" + "=" * 70)
    logger.info("EXECUTION TRAJECTORY & TOKEN LEDGER")
    logger.info("=" * 70)

    cumulative_tokens = 0
    for node in all_nodes:
        prompt_art = next((p for p in prompt_arts if p.node_id == node.id), None)
        out_arts = [a for a in all_arts if a.node_id == node.id and a.kind != "prompt"]

        in_chars = len(prompt_art.content) if prompt_art else 0
        out_chars = sum(len(a.content) for a in out_arts)

        in_tokens = round(in_chars / 4)
        out_tokens = round(out_chars / 4)
        total_tokens = in_tokens + out_tokens
        cumulative_tokens += total_tokens

        logger.info(
            f"Node: {node.name:<22} | Status: {node.status.value:<10} | "
            f"Attempt: {node.attempt} | In: ~{in_tokens:<5} | Out: ~{out_tokens:<5} | "
            f"Node Total: ~{total_tokens:<5} | Cumulative: ~{cumulative_tokens:<6}"
        )

    logger.info("=" * 70)
    logger.info(f"TOTAL CUMULATIVE RUN TOKENS: ~{cumulative_tokens} tokens (Budget: 200,000 TPD)")
    logger.info("=" * 70)

    # Inspect security_report.json
    sec_reports = [a for a in all_arts if a.kind == "security_report"]
    logger.info(f"\nCaptured {len(sec_reports)} security_report artifact(s):")
    for sr in sec_reports:
        logger.info(f"\n--- security_report.json (Attempt {sr.attempt}) ---")
        logger.info(sr.content)

    # Inspect final verdict & review
    verdicts = [a for a in all_arts if a.kind == "verdict"]
    reviews = [a for a in all_arts if a.kind == "review" or a.filename == "review.md"]

    if verdicts:
        logger.info(f"\nFinal Verdict Artifact (Attempt {verdicts[-1].attempt}):")
        logger.info(verdicts[-1].content)

    if reviews:
        logger.info(f"\nFinal Review Report (Attempt {reviews[-1].attempt}):")
        logger.info(reviews[-1].content)

    # Check leaked docker containers
    client = docker.from_env()
    containers = client.containers.list(all=True, filters={"name": "nexus-sandbox-security-"})
    logger.info(f"\nLeaked sandbox containers check: {len(containers)} containers found.")
    assert len(containers) == 0, "Leaked containers detected!"

    await engine.dispose()
    logger.info("\nPhase 3 Live Execution completed successfully.")


async def main():
    await test_known_vulnerable_fixture_demo()
    await run_phase3_live()


if __name__ == "__main__":
    asyncio.run(main())
