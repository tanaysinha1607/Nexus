"""Docker Sandbox Executor Node handler."""

import json
import logging
from app.models import Artifact, Node, NodeStatus
from orchestrator.config import HandlerConfig
from orchestrator.handlers import ArtifactSpec, HandlerResult
from orchestrator.sandbox.docker_runner import run_code_in_docker_sandbox
from orchestrator.sandbox.test_runner import run_contract_tests_in_docker_sandbox
from orchestrator.sandbox.ts_build_runner import run_ts_build_in_docker_sandbox

logger = logging.getLogger(__name__)


async def handle_executor_node(
    node: Node,
    inputs: dict[str, Artifact],
    config: HandlerConfig,
) -> HandlerResult:
    """Execute code, contract tests, or TS builds in a Docker sandbox container and emit corresponding report artifacts."""
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
    test_files: dict[str, str] = {}
    frontend_files: dict[str, str] = {}
    for art in inputs.values():
        if art.kind == "source_code":
            source_files[art.filename] = art.content
        elif art.kind == "test_code":
            test_files[art.filename] = art.content
        elif art.kind == "frontend_code":
            frontend_files[art.filename] = art.content

    # Branch for BuildExecutor (TypeScript compile validation)
    if node.agent_role == "build_executor" or (node.name == "BuildExecutor" and bool(frontend_files)):
        if "mock_report" in node.config:
            report = node.config["mock_report"]
        elif not frontend_files:
            report = {
                "build_attempted": False,
                "tsc_exit_code": 1,
                "type_errors": 1,
                "compiled_ok": False,
                "tsc_output_tail": "Missing frontend_code artifacts.",
                "elapsed_s": 0.0,
            }
        else:
            report = run_ts_build_in_docker_sandbox(frontend_files)

        artifact_spec = ArtifactSpec(
            kind="build_report",
            filename="build_report.json",
            content=json.dumps(report, indent=2),
            content_type="application/json",
        )

        log_msg = (
            f"BuildExecutor {node.name} completed TS build execution: "
            f"compiled_ok={report.get('compiled_ok')}, "
            f"tsc_exit_code={report.get('tsc_exit_code')}, type_errors={report.get('type_errors')}"
        )

        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=log_msg,
        )

    # Branch for TestExecutor
    if node.agent_role == "test_executor" or bool(test_files):
        if "mock_report" in node.config:
            report = node.config["mock_report"]
        elif not source_files or not test_files:
            report = {
                "build_success": False,
                "build_logs_tail": "Missing source_code or test_code artifacts.",
                "container_started": False,
                "service_booted": False,
                "tests_collected": 0,
                "passed": 0,
                "failed": 0,
                "exit_code": 1,
                "pytest_output_tail": "Missing source_code or test_code artifacts.",
                "elapsed_s": 0.0,
            }
        else:
            report = run_contract_tests_in_docker_sandbox(source_files, test_files)

        artifact_spec = ArtifactSpec(
            kind="test_report",
            filename="test_report.json",
            content=json.dumps(report, indent=2),
            content_type="application/json",
        )

        log_msg = (
            f"TestExecutor {node.name} completed contract test execution: "
            f"service_booted={report.get('service_booted')}, "
            f"passed={report.get('passed')}, failed={report.get('failed')}"
        )

        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=log_msg,
        )

    # Standard Smoke Executor
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
