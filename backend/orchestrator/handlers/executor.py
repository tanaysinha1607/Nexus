"""Docker Sandbox Executor Node handler."""

import json
import logging
from app.models import Artifact, Node, NodeStatus
from orchestrator.config import HandlerConfig
from orchestrator.handlers import ArtifactSpec, HandlerResult
from orchestrator.sandbox.docker_runner import run_code_in_docker_sandbox

logger = logging.getLogger(__name__)


async def handle_executor_node(
    node: Node,
    inputs: dict[str, Artifact],
    config: HandlerConfig,
) -> HandlerResult:
    """Execute code in a Docker sandbox container and emit an execution_report artifact."""
    # Phase 0 backward compatibility for legacy fake executor tests
    if "exit_code" in node.config or ("stdout" in node.config and "mock_report" not in node.config):
        exit_code = node.config.get("exit_code", 0)
        stdout_msg = node.config.get("stdout", f"Execution finished for node {node.name}")
        payload = {"exit_code": exit_code, "stdout": stdout_msg}
        artifact_spec = ArtifactSpec(
            kind="stdout",
            filename=f"stdout_{node.name}.json",
            content=json.dumps(payload),
            content_type="application/json",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"Executor {node.name} finished with exit_code {exit_code}.",
        )

    source_files: dict[str, str] = {}
    for art in inputs.values():
        if art.kind == "source_code":
            source_files[art.filename] = art.content

    if not source_files:
        if "mock_report" in node.config:
            report = node.config["mock_report"]
        else:
            report = {
                "build_success": False,
                "build_logs_tail": "No source_code artifacts found in input.",
                "container_started": False,
                "health_status_code": None,
                "health_ok": False,
                "elapsed_s": 0.0,
                "container_logs_tail": "",
            }
    else:
        if "mock_report" in node.config:
            report = node.config["mock_report"]
        else:
            report = run_code_in_docker_sandbox(source_files)

    artifact_spec = ArtifactSpec(
        kind="execution_report",
        filename="execution_report.json",
        content=json.dumps(report, indent=2),
        content_type="application/json",
    )

    log_msg = (
        f"Executor {node.name} completed sandbox execution: "
        f"build_success={report.get('build_success')}, "
        f"health_ok={report.get('health_ok')}"
    )

    return HandlerResult(
        status=NodeStatus.completed,
        artifacts=[artifact_spec],
        logs=log_msg,
    )
