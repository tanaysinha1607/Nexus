"""Phase 2a Live Execution Script: QA Engineer & Black-Box Integration Tests.

Runs the `pm_arch_backend_qa` DAG with real Groq LLM + Docker sandbox.
Monitors node transitions, prints the token budget ledger per node,
inspects `test_report.json` verbatim, checks contract test results,
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
from orchestrator.scheduler import RunScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase2a_live")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://nexus:nexus_dev@postgres:5432/nexus",
)


async def run_phase2a_live():
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    logger.info("=" * 70)
    logger.info("NEXUS PHASE 2a LIVE EXECUTION RUN")
    logger.info(f"Project ID: {project_id}")
    logger.info(f"Run ID:     {run_id}")
    logger.info("=" * 70)

    user_prompt = (
        "Build a lightweight FastAPI service for an automated portfolio management engine. "
        "Must implement POST /api/v1/auth/register, POST /api/v1/auth/login, "
        "and GET /api/v1/portfolio/summary. "
        "Load secrets from environment variables (os.environ['JWT_SECRET_KEY']). "
        "Include passlib[bcrypt] in requirements.txt and use bcrypt for password hashing."
    )

    async with session_factory() as session:
        proj = Project(id=project_id, name="Phase2a Live Test", user_prompt=user_prompt)
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

        # Create pm_arch_backend_qa graph nodes
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
        qa_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="QA",
            node_type="agent", agent_role="qa_engineer", config={"required_inputs": [{"kind": "api_contract"}]}
        )
        exec_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="TestExecutor",
            node_type="executor", agent_role="test_executor", config={"required_inputs": [{"kind": "source_code"}, {"kind": "test_code"}]}
        )
        val_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="TestValidator",
            node_type="validator", agent_role="test_validator", config={"required_inputs": [{"kind": "test_report"}]}
        )
        reviewer_node = Node(
            id=uuid.uuid4(), project_id=project_id, run_id=run_id, name="Reviewer",
            node_type="agent", agent_role="senior_reviewer",
            config={"required_inputs": [{"kind": "verdict"}, {"kind": "source_code"}, {"kind": "api_contract"}]}
        )

        nodes = [pm_node, arch_node, api_node, backend_node, qa_node, exec_node, val_node, reviewer_node]
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
        # Fetch all nodes
        n_stmt = select(Node).where(Node.run_id == run_id).order_by(Node.created_at.asc())
        n_res = await session.execute(n_stmt)
        all_nodes = list(n_res.scalars().all())

        # Fetch prompt artifacts for token estimates
        p_stmt = select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "prompt")
        p_res = await session.execute(p_stmt)
        prompt_arts = list(p_res.scalars().all())

        # Fetch output artifacts
        o_stmt = select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.asc())
        o_res = await session.execute(o_stmt)
        all_arts = list(o_res.scalars().all())

    logger.info("\n" + "=" * 70)
    logger.info("EXECUTION TRAJECTORY & TOKEN LEDGER")
    logger.info("=" * 70)

    cumulative_tokens = 0
    for node in all_nodes:
        # Approximate tokens from character length (1 token ~ 4 chars)
        prompt_art = next((p for p in prompt_arts if p.node_id == node.id), None)
        out_arts = [a for a in all_arts if a.node_id == node.id and a.kind != "prompt"]

        in_chars = len(prompt_art.content) if prompt_art else 0
        out_chars = sum(len(a.content) for a in out_arts)

        in_tokens = round(in_chars / 4)
        out_tokens = round(out_chars / 4)
        total_tokens = in_tokens + out_tokens
        cumulative_tokens += total_tokens

        logger.info(
            f"Node: {node.name:<18} | Status: {node.status.value:<10} | "
            f"Attempt: {node.attempt} | In: ~{in_tokens:<5} | Out: ~{out_tokens:<5} | "
            f"Node Total: ~{total_tokens:<5} | Cumulative: ~{cumulative_tokens:<6}"
        )

    logger.info("=" * 70)
    logger.info(f"TOTAL CUMULATIVE RUN TOKENS: ~{cumulative_tokens} tokens (Budget: 200,000 TPD)")
    logger.info("=" * 70)

    # Inspect test_report.json
    test_reports = [a for a in all_arts if a.kind == "test_report"]
    logger.info(f"\nCaptured {len(test_reports)} test_report artifact(s):")
    for tr in test_reports:
        logger.info(f"\n--- test_report.json (Attempt {tr.attempt}) ---")
        logger.info(tr.content)

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
    containers = client.containers.list(all=True, filters={"name": "nexus-sb-"})
    logger.info(f"\nLeaked sandbox containers check: {len(containers)} containers found.")
    assert len(containers) == 0, "Leaked containers detected!"

    await engine.dispose()
    logger.info("\nPhase 2a Live Execution completed successfully.")


if __name__ == "__main__":
    asyncio.run(run_phase2a_live())
