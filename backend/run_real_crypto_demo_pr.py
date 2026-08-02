"""Phase 5 Live Script: Real Crypto Trading Platform Demo PR (PR #2).

Executes a real pm_arch_backend_security DAG with Groq LLM and Docker sandbox
for the canonical cryptocurrency paper trading platform prompt, lets it self-heal
and verify, opens the real GitHub PR, and reports all verified PR details.
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
logger = logging.getLogger("crypto_demo_pr")

CANONICAL_PROMPT = (
    "Build a cryptocurrency paper trading platform with authentication, a dashboard, "
    "charts, portfolio management, and an admin panel."
)


async def main():
    print("=" * 80)
    print("NEXUS PHASE 5 -- REAL DEMO PULL REQUEST GENERATION (PR #2)")
    print("=" * 80)
    print(f"Target Repo: {os.getenv('GITHUB_OUTPUT_REPO')}")
    print(f"Prompt: {CANONICAL_PROMPT}")

    # 1. Create Project & Run
    async with async_session() as session:
        project_id = uuid.uuid4()
        project = Project(
            id=project_id,
            name="Crypto Paper Trading Platform",
            user_prompt=CANONICAL_PROMPT,
        )
        session.add(project)
        await session.commit()

        run_obj = await create_run(
            project_id=project_id,
            request=None,
            graph="pm_arch_backend_security",
            db=session,
        )
        run_id = run_obj.id
        print(f"\nCreated Run ID: {run_id} for Project ID: {project_id}")

    # 2. Configure Scheduler & Launch DAG Execution
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

    print("\nStarting Real Agent Execution Loop (pm_arch_backend_security)...")
    scheduler_task = asyncio.create_task(scheduler.run())

    start_time = asyncio.get_event_loop().time()
    while True:
        await asyncio.sleep(1.0)
        async with async_session() as session:
            r = await session.get(Run, run_id)
            if r.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
                print(f"\nDAG Execution finished with status: {r.status.value}")
                break
        if asyncio.get_event_loop().time() - start_time > 300:
            print("\nERROR: Run timed out after 300 seconds!")
            sys.exit(1)

    await scheduler_task

    # 3. Verify Verdict & Open GitHub PR
    print("\n" + "=" * 80)
    print("VERIFYING GATE OUTCOMES & OPENING GITHUB PULL REQUEST")
    print("=" * 80)

    async with async_session() as session:
        res = await session.execute(
            select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "verdict").order_by(Artifact.attempt.desc())
        )
        verdicts = list(res.scalars().all())
        if not verdicts:
            print("ERROR: No verdict artifact found!")
            sys.exit(1)

        v_data = json.loads(verdicts[0].content)
        print(f"Final Attempt ({verdicts[0].attempt}) Verdict: passed={v_data.get('passed')}, reviewer={v_data.get('reviewer_verdict')}")

        if not v_data.get("passed"):
            print(f"ERROR: Verification failed! Failures: {v_data.get('failures')}")
            sys.exit(1)

        try:
            pr_url = await open_github_pr(session, run_id)
            print(f"\n✓ SUCCESS! Real Demo GitHub PR Opened: {pr_url}")
        except Exception as e:
            print(f"ERROR opening PR: {e}")
            sys.exit(1)

        run_refreshed = await session.get(Run, run_id)
        print(f"DB Run pr_url: {run_refreshed.pr_url}")

        # 4. Secret Leak Audit Across DB Artifacts
        all_arts = await session.execute(
            select(Artifact).where(Artifact.run_id == run_id)
        )
        token = os.getenv("GITHUB_TOKEN", "")
        token_leaked = False
        if token:
            for art in all_arts.scalars().all():
                if token in art.content:
                    token_leaked = True
                    print(f"SECURITY AUDIT ERROR: Token found in artifact {art.filename}!")
        if not token_leaked:
            print("✓ SECURITY AUDIT PASSED: GITHUB_TOKEN appears NOWHERE in DB artifacts.")

    # 5. Fetch PR Details & Committed Files from GitHub REST API
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_OUTPUT_REPO")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get PR list
        pulls_resp = await client.get(f"https://api.github.com/repos/{repo}/pulls?state=open", headers=headers)
        prs = pulls_resp.json()
        latest_pr = prs[0]
        pr_number = latest_pr["number"]
        head_branch = latest_pr["head"]["ref"]

        print("\n" + "=" * 80)
        print(f"REAL DEMO PULL REQUEST (PR #{pr_number})")
        print("=" * 80)
        print(f"URL:        {latest_pr['html_url']}")
        print(f"Title:      {latest_pr['title']}")
        print(f"Branch:     {head_branch} -> {latest_pr['base']['ref']}")
        print("\n--- PR BODY VERBATIM ---")
        print(latest_pr["body"])

        # Fetch committed main.py on head branch
        file_resp = await client.get(
            f"https://api.github.com/repos/{repo}/contents/main.py?ref={head_branch}",
            headers=headers,
        )
        if file_resp.status_code == 200:
            main_content = base64.b64decode(file_resp.json()["content"]).decode("utf-8")
            first_lines = "\n".join(main_content.splitlines()[:25])
            print("\n--- COMMITTED main.py (FIRST 25 LINES) ---")
            print(first_lines)
            print("------------------------------------------")
        else:
            print(f"Could not fetch main.py: {file_resp.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
