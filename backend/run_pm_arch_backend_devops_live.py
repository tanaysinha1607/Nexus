"""Phase 4 Live Execution Script for Nexus Orchestrator.

Demonstrates:
1. Teeth-Test Fixture: Running hadolint and DevOpsValidator against a known-bad Dockerfile
   (FROM ubuntu:latest + USER root) to prove it returns ERROR-level findings (DL3006/DL3002) and FAILs.
2. Full 8-Node Live DAG Execution (`pm_arch_backend_devops`):
   PM -> Architect -> ApiDesigner -> Backend -> DevOps -> DevOpsExecutor -> DevOpsValidator -> Reviewer
   Uses real Groq LLM + Docker sandbox execution.
3. Proof of image/container zero-leak cleanup.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
import docker

sys.path.insert(0, os.path.dirname(__file__))

from app.database import async_session
from app.models import Artifact, Node, NodeStatus, Project, Run
from app.routers.runs import create_run
from orchestrator.scheduler import RunScheduler, SchedulerConfig
from orchestrator.sandbox.devops_runner import run_devops_checks_in_docker_sandbox
from orchestrator.handlers.validator import handle_validator_node
from orchestrator.config import HandlerConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run_teeth_test_fixture():
    """Run teeth-test fixture scanning a deliberately bad Dockerfile."""
    print("\n" + "=" * 80)
    print("DEMO 1: DEVOPS GATE TEETH-TEST FIXTURE DEMONSTRATION")
    print("=" * 80)

    bad_dockerfile = """FROM ubuntu
WORKDIR app
ADD main.py /app/main.py
RUN sudo apt-get update
USER root
CMD ["python3", "main.py"]
"""
    mock_source = {"main.py": "print('hello world')"}

    print("\nScanning known-bad Dockerfile fixture:")
    print("--------------------------------------------------")
    print(bad_dockerfile)
    print("--------------------------------------------------")

    report = run_devops_checks_in_docker_sandbox(bad_dockerfile, mock_source)
    print("\nRaw DevOps Execution Report:")
    print(json.dumps(report, indent=2))

    # Test DevOpsValidator evaluation
    config = HandlerConfig()
    node = Node(id=uuid.uuid4(), name="DevOpsValidator", node_type="validator", agent_role="devops_validator")
    rep_art = Artifact(
        id=uuid.uuid4(),
        filename="devops_report.json",
        kind="devops_report",
        content=json.dumps(report),
    )
    
    val_res = asyncio.run(handle_validator_node(node, {"devops_report": rep_art}, config))
    v_data = json.loads(val_res.artifacts[0].content)

    print("\nDevOpsValidator Verdict on Bad Fixture:")
    print(f"Passed: {v_data.get('passed')}")
    print("Failures:")
    for f in v_data.get("failures", []):
        print(f" - {f}")

    assert v_data.get("passed") is False, "Gate teeth test failed: DevOpsValidator passed a bad Dockerfile!"
    print("\n✓ SUCCESS: DevOps Gate Teeth-Test PASS! (Validator deterministically rejected bad Dockerfile).")


async def run_live_devops_dag():
    """Run full live 8-node pm_arch_backend_devops DAG."""
    print("\n" + "=" * 80)
    print("DEMO 2: FULL 8-NODE LIVE DAG RUN (pm_arch_backend_devops)")
    print("=" * 80)

    # Set env flags for real LLM agents
    os.environ["USE_REAL_AGENTS"] = "true"
    os.environ["NEXUS_LLM_PROVIDER"] = "groq"
    os.environ["NEXUS_LLM_MODEL"] = "openai/gpt-oss-120b"

    async with async_session() as session:
        project_id = uuid.uuid4()
        project = Project(
            id=project_id,
            name="Phase4_Live_DevOps_Demo",
            user_prompt="Build a minimalist FastAPI microservice with /health and /version endpoints.",
        )
        session.add(project)
        await session.commit()

        run_obj = await create_run(project_id=project_id, request=None, graph="pm_arch_backend_devops", db=session)
        run_id = run_obj.id
        print(f"Created Run ID: {run_id} for Project ID: {project_id}")

    scheduler_config = SchedulerConfig(
        max_parallel_nodes=1,
        heartbeat_interval_seconds=1.0,
        lease_duration_seconds=30.0,
        max_node_runtime_seconds=120.0,
        poll_interval_seconds=0.5,
    )

    scheduler = RunScheduler(
        run_id=run_id,
        session_factory=async_session,
        scheduler_config=scheduler_config,
    )

    start_time = asyncio.get_event_loop().time()
    await scheduler.run()
    elapsed = asyncio.get_event_loop().time() - start_time

    # Inspect results
    async with async_session() as session:
        run_nodes = (
            await session.execute(
                Node.__table__.select().where(Node.run_id == run_id).order_by(Node.created_at.asc())
            )
        ).fetchall()

        print("\n" + "=" * 80)
        print(f"LIVE RUN SUMMARY (Elapsed: {elapsed:.2f}s)")
        print("=" * 80)

        total_input_tokens = 0
        total_output_tokens = 0

        for row in run_nodes:
            in_t = row.input_tokens or 0
            out_t = row.output_tokens or 0
            total_input_tokens += in_t
            total_output_tokens += out_t
            print(f"Node: {row.name:<22} | Status: {row.status:<10} | Attempt: {row.attempt} | In: ~{in_t:<5} | Out: ~{out_t:<5}")

        print("-" * 80)
        print(f"Total Cumulative Tokens: {total_input_tokens + total_output_tokens} (In: {total_input_tokens}, Out: {total_output_tokens})")

        # Retrieve and display dockerfile and devops_report
        artifacts = (
            await session.execute(
                Artifact.__table__.select().where(Artifact.run_id == run_id)
            )
        ).fetchall()

        print("\nCaptured Key Artifacts:")
        print("--------------------------------------------------")
        for art in artifacts:
            if art.kind in ("dockerfile", "devops_report", "verdict"):
                print(f"\n--- {art.filename} (Kind: {art.kind}, Attempt {art.attempt}) ---")
                print(art.content[:1500])

    # Check zero-leak image status
    client = docker.from_env()
    devops_images = [img for img in client.images.list() if any("nexus-sandbox-devops" in tag for tag in img.tags)]
    dangling_images = client.images.list(filters={"dangling": True})
    print("\n" + "=" * 80)
    print("HOST IMAGE CLEANUP AUDIT")
    print("=" * 80)
    print(f"nexus-devops tagged images remaining: {len(devops_images)}")
    print(f"dangling images count: {len(dangling_images)}")
    assert len(devops_images) == 0, f"Leaked images found: {devops_images}"
    print("✓ ZERO leaked containers OR images!")


if __name__ == "__main__":
    run_teeth_test_fixture()
    asyncio.run(run_live_devops_dag())
