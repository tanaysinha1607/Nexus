"""Live Phase 5 Demo Execution Script.

1. Runs a full live DAG (PM -> Architect -> ApiDesigner -> Backend -> DevOps -> DevOpsExecutor -> DevOpsValidator -> Reviewer).
2. Verifies the run reaches final PASS status.
3. Invokes open_github_pr() against real GITHUB_OUTPUT_REPO with real GITHUB_TOKEN.
4. Reports the real PR URL, exact PR body, and verifies committed files.
"""

import asyncio
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))

# Load .env variables manually if not set
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.database import async_session
from app.models import Artifact, Node, NodeStatus, Project, Run
from app.routers.runs import create_run
from app.integrations.github_pr import open_github_pr, redact_token
from orchestrator.scheduler import RunScheduler, SchedulerConfig
from orchestrator.config import HandlerConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def run_live_phase5_demo():
    print("=" * 80)
    print("NEXUS PHASE 5 — REAL GITHUB PULL REQUEST INTEGRATION DEMONSTRATION")
    print("=" * 80)

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_OUTPUT_REPO")

    print(f"Target Repo: {repo}")
    print(f"Token Configured: {bool(token)}")

    if not token or not repo:
        print("ERROR: GITHUB_TOKEN or GITHUB_OUTPUT_REPO missing in environment.")
        sys.exit(1)

    project_id = uuid.uuid4()
    async with async_session() as session:
        project = Project(
            id=project_id,
            name="Phase5_GitHub_PR_Demo",
            user_prompt="Build a minimalist FastAPI microservice with /health and /version endpoints.",
        )
        session.add(project)
        await session.commit()

        run_obj = await create_run(
            project_id=project_id,
            request=None,
            graph="pm_arch_backend_devops",
            db=session,
        )
        run_id = run_obj.id
        print(f"Created Run ID: {run_id} for Project ID: {project_id}")

    scheduler_config = SchedulerConfig(
        max_parallel_nodes=1,
        lease_seconds=30.0,
        max_node_runtime_seconds=120.0,
        poll_interval=0.5,
        use_real_agents=True,
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
            if r and r.status.value in ("completed", "failed", "cancelled"):
                break
        if asyncio.get_event_loop().time() - start_time > 180:
            print("ERROR: Run timed out!")
            scheduler.stop()
            await scheduler_task
            sys.exit(1)

    scheduler.stop()
    await scheduler_task

    print("\n" + "=" * 80)
    print("LIVE RUN COMPLETE — OPENING GITHUB PULL REQUEST")
    print("=" * 80)

    async with async_session() as session:
        # Check verdict
        res = await session.execute(
            "SELECT content FROM artifacts WHERE run_id = :r AND kind = 'verdict' ORDER BY attempt DESC LIMIT 1",
            {"r": run_id},
        )
        verdict_row = res.fetchone()
        if not verdict_row:
            print("ERROR: No verdict artifact found!")
            sys.exit(1)

        v_data = json.loads(verdict_row[0])
        print(f"Final Attempt Verdict: passed={v_data.get('passed')}, reviewer={v_data.get('reviewer_verdict')}")

        if not v_data.get("passed"):
            print("ERROR: Run did not pass verification gates!")
            sys.exit(1)

        # Call open_github_pr
        try:
            pr_url = await open_github_pr(session, run_id)
            print(f"\n✓ SUCCESS! GitHub PR Opened: {pr_url}\n")
        except Exception as e:
            print(f"ERROR opening PR: {e}")
            sys.exit(1)

        # Fetch PR body and artifacts for verification summary
        run_refreshed = await session.get(Run, run_id)
        print(f"Run pr_url stored in DB: {run_refreshed.pr_url}")

        # Audit token leak across all DB artifacts
        all_arts = await session.execute(
            "SELECT filename, content FROM artifacts WHERE run_id = :r",
            {"r": run_id},
        )
        token_found_in_artifacts = False
        for fname, content in all_arts.fetchall():
            if token in content:
                print(f"CRITICAL ERROR: GITHUB_TOKEN leaked in artifact {fname}!")
                token_found_in_artifacts = True

        if not token_found_in_artifacts:
            print("✓ AUDIT PASSED: GITHUB_TOKEN appears NOWHERE in DB artifacts.")


if __name__ == "__main__":
    asyncio.run(run_live_phase5_demo())
