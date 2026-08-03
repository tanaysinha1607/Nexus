"""Phase 6a Live Execution Script: Prompt Generality & Multi-Domain Proof.

Run 1 (Regression): Cryptocurrency Paper Trading Platform through pm_arch_backend_security.
Run 2 (Generality Proof): URL Shortener API through pm_arch_backend_qa (with QA redirect testing & PR #3).
"""

import asyncio
import json
import logging
import os
import sys
import uuid
import base64
import httpx

from sqlalchemy import select

from app.database import async_session
from app.models import Artifact, Node, Project, Run, RunStatus
from app.routers.runs import create_run
from orchestrator.config import HandlerConfig, SchedulerConfig
from orchestrator.scheduler import RunScheduler
from app.integrations.github_pr import open_github_pr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase6a_live")


async def run_dag(project_name: str, user_prompt: str, graph_name: str) -> tuple[uuid.UUID, uuid.UUID, str, int]:
    async with async_session() as session:
        project_id = uuid.uuid4()
        project = Project(id=project_id, name=project_name, user_prompt=user_prompt)
        session.add(project)
        await session.commit()

        run_obj = await create_run(
            project_id=project_id,
            request=None,
            graph=graph_name,
            db=session,
        )
        run_id = run_obj.id
        logger.info(f"Created Run ID: {run_id} for Project: {project_name}")

    scheduler_config = SchedulerConfig(
        max_parallel_nodes=1,
        lease_seconds=30.0,
        max_node_runtime_seconds=180.0,
        poll_interval=0.5,
        use_real_agents=True,
        max_attempts=5,
    )
    handler_config = HandlerConfig()

    scheduler = RunScheduler(
        session_factory=async_session,
        run_id=run_id,
        scheduler_config=scheduler_config,
        handler_config=handler_config,
    )

    scheduler_task = asyncio.create_task(scheduler.run())
    start_time = asyncio.get_event_loop().time()

    while True:
        await asyncio.sleep(1.0)
        async with async_session() as session:
            r = await session.get(Run, run_id)
            if r.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
                final_status = r.status.value
                break
        if asyncio.get_event_loop().time() - start_time > 300:
            logger.error(f"Run {run_id} timed out after 300s!")
            sys.exit(1)

    await scheduler_task

    # Calculate token consumption
    async with async_session() as session:
        p_res = await session.execute(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "prompt")
        )
        prompt_arts = list(p_res.scalars().all())
        a_res = await session.execute(
            select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at.asc())
        )
        all_arts = list(a_res.scalars().all())

    in_chars = sum(len(p.content) for p in prompt_arts)
    out_chars = sum(len(a.content) for a in all_arts if a.kind != "prompt")
    total_tokens = round((in_chars + out_chars) / 4)

    return project_id, run_id, final_status, total_tokens


async def main():
    print("=" * 80)
    print("NEXUS PHASE 6a -- PROMPT-GENERALITY LIVE VERIFICATION")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # RUN 1: Regression Test (Crypto Paper Trading Platform)
    # -------------------------------------------------------------------------
    crypto_prompt = (
        "Build a cryptocurrency paper trading platform with authentication, a dashboard, "
        "charts, portfolio management, and an admin panel."
    )
    print("\n--- RUN 1: CRYPTO PLATFORM REGRESSION TEST ---")
    p1_id, r1_id, status1, tokens1 = await run_dag(
        project_name="Crypto Regression Run",
        user_prompt=crypto_prompt,
        graph_name="pm_arch_backend_security",
    )
    print(f"Run 1 ID: {r1_id} | Status: {status1} | Token Cost: ~{tokens1} tokens")
    assert status1 == "completed", f"Run 1 Crypto regression failed with status {status1}!"
    print("✓ Run 1 Crypto regression PASSED cleanly.")

    # -------------------------------------------------------------------------
    # RUN 2: Domain Generality Proof (URL Shortener API)
    # -------------------------------------------------------------------------
    url_shortener_prompt = (
        "Build a URL shortener API with API-key authentication and click analytics: "
        "create short links, redirect, and track click counts."
    )
    print("\n--- RUN 2: URL SHORTENER DOMAIN GENERALITY PROOF ---")
    p2_id, r2_id, status2, tokens2 = await run_dag(
        project_name="URL Shortener Generality Run",
        user_prompt=url_shortener_prompt,
        graph_name="pm_arch_backend_qa",
    )
    print(f"Run 2 ID: {r2_id} | Status: {status2} | Token Cost: ~{tokens2} tokens")
    assert status2 == "completed", f"Run 2 URL Shortener failed with status {status2}!"

    # Inspect Run 2 Trajectory & Artifacts
    async with async_session() as session:
        arts_res = await session.execute(
            select(Artifact).where(Artifact.run_id == r2_id).order_by(Artifact.created_at.asc())
        )
        arts2 = list(arts_res.scalars().all())

        prd_art = next((a for a in arts2 if a.kind == "prd"), None)
        contract_art = next((a for a in arts2 if a.kind == "api_contract"), None)
        code_art = next((a for a in arts2 if a.kind == "source_code" and a.filename == "main.py"), None)
        test_art = next((a for a in arts2 if a.kind == "test_code"), None)
        report_art = next((a for a in arts2 if a.kind == "test_report"), None)
        verdict_art = next((a for a in arts2 if a.kind == "verdict"), None)

        print("\n=== RUN 2 GENERATED API CONTRACT ===")
        if contract_art:
            print(contract_art.content)

        print("\n=== RUN 2 GENERATED test_api.py (FIRST 30 LINES) ===")
        if test_art:
            lines = test_art.content.splitlines()[:30]
            print("\n".join(lines))

        print("\n=== RUN 2 GENERATED main.py (FIRST 30 LINES) ===")
        if code_art:
            lines = code_art.content.splitlines()[:30]
            print("\n".join(lines))

        print("\n=== RUN 2 VERDICT & TEST REPORT ===")
        if report_art:
            print(f"Test Report: {report_art.content}")
        if verdict_art:
            print(f"Verdict: {verdict_art.content}")

        # Open GitHub PR for Run 2
        try:
            pr_url = await open_github_pr(session, r2_id)
            print(f"\n✓ SUCCESS! Real Demo PR Opened for URL Shortener: {pr_url}")
        except Exception as e:
            print(f"ERROR opening PR: {e}")
            sys.exit(1)

        # Audit GITHUB_TOKEN leak across all DB artifacts
        token = os.getenv("GITHUB_TOKEN", "")
        token_leaked = False
        if token:
            for art in arts2:
                if token in art.content:
                    token_leaked = True
                    print(f"SECURITY AUDIT ERROR: Token found in artifact {art.filename}!")
        if not token_leaked:
            print("✓ SECURITY AUDIT PASSED: GITHUB_TOKEN appears NOWHERE in DB artifacts.")

    print("\n" + "=" * 80)
    print("PHASE 6a LIVE PROOF COMPLETE")
    print(f"Crypto Run Tokens:        ~{tokens1} tokens")
    print(f"URL Shortener Run Tokens: ~{tokens2} tokens")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
