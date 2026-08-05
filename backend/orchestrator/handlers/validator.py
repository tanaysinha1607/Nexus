"""Deterministic Smoke Validator Node handler."""

import json
import logging
from app.models import Artifact, Node, NodeStatus
from orchestrator.config import HandlerConfig
from orchestrator.handlers import ArtifactSpec, HandlerResult

logger = logging.getLogger(__name__)


async def handle_validator_node(
    node: Node,
    inputs: dict[str, Artifact],
    config: HandlerConfig,
) -> HandlerResult:
    """Execute a deterministic Validator node.

    Supports both Phase 1.3b execution_report and Phase 0 legacy stdout artifacts.
    NO LLM. Node status is completed in both pass and fail cases.
    """
    devops_report_art = None
    security_report_art = None
    build_report_art = None
    test_report_art = None
    exec_report_art = None
    stdout_art = None
    for art in inputs.values():
        if art.kind == "devops_report":
            devops_report_art = art
        elif art.kind == "security_report":
            security_report_art = art
        elif art.kind == "build_report":
            build_report_art = art
        elif art.kind == "test_report":
            test_report_art = art
        elif art.kind == "execution_report":
            exec_report_art = art
        elif art.kind == "stdout":
            stdout_art = art

    # DevOpsValidator branch (Phase 4 hadolint + docker build check)
    if devops_report_art is not None:
        try:
            report = json.loads(devops_report_art.content)
        except Exception as exc:
            return HandlerResult(
                status=NodeStatus.failed,
                artifacts=[],
                logs=f"Validator error parsing devops_report JSON: {exc}",
            )

        hadolint_ran = report.get("hadolint_ran", False)
        error_count = report.get("error_count", 0)
        hadolint_findings = report.get("hadolint_findings", [])
        build_success = report.get("build_success", False)
        build_logs_tail = report.get("build_logs_tail", "")

        passed = bool(hadolint_ran and error_count == 0 and build_success)
        failures = []
        if not hadolint_ran:
            failures.append("hadolint_linter_failed_to_run")
        if error_count > 0:
            for f in hadolint_findings:
                loc = f"line {f.get('line')}" if f.get("line") else "Dockerfile"
                failures.append(f"HADOLINT_ERROR [{f.get('code')}] {f.get('message')} at {loc}")
        if not build_success:
            failures.append(f"DOCKER_BUILD_FAILED: {build_logs_tail[-300:]}")

        verdict_payload = {
            "passed": passed,
            "failures": failures,
        }
        artifact_spec = ArtifactSpec(
            kind="verdict",
            filename="verdict.json",
            content=json.dumps(verdict_payload, indent=2),
            content_type="application/json",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"DevOpsValidator verdict: passed={passed}, failures={failures}",
            meta={"passed": passed, "failures": failures},
        )

    # SecurityValidator branch (Bandit for Python, npm audit for Node)
    if security_report_art is not None:
        try:
            report = json.loads(security_report_art.content)
        except Exception as exc:
            return HandlerResult(
                status=NodeStatus.failed,
                artifacts=[],
                logs=f"Validator error parsing security_report JSON: {exc}",
            )

        scan_completed = report.get("scan_completed", False)
        high_count = report.get("high_count", 0)
        critical_count = report.get("critical_count", 0)
        high_findings = report.get("high_findings", [])
        scanner = report.get("scanner", "bandit")

        passed = bool(scan_completed and high_count == 0 and critical_count == 0)
        failures = []
        if not scan_completed:
            failures.append("security_scan_failed_to_complete")
        if high_count > 0 or critical_count > 0:
            for f in high_findings:
                if scanner == "npm_audit":
                    pkg = f.get("package", f.get("name", "unknown"))
                    sev = f.get("severity", "high").upper()
                    title = f.get("title", "Vulnerable dependency")
                    failures.append(f"{sev}_DEPENDENCY_VULNERABILITY [{pkg}] {title}")
                else:
                    loc = f"{f.get('filename')}:{f.get('line_number')}"
                    failures.append(f"HIGH_VULNERABILITY [{f.get('test_id')}] {f.get('issue_text')} at {loc}")

        verdict_payload = {
            "passed": passed,
            "failures": failures,
        }
        artifact_spec = ArtifactSpec(
            kind="verdict",
            filename="verdict.json",
            content=json.dumps(verdict_payload, indent=2),
            content_type="application/json",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"SecurityValidator verdict: passed={passed}, failures={failures}",
            meta={"passed": passed, "failures": failures},
        )

    # BuildValidator branch (Phase 2b TypeScript compiler check)
    if build_report_art is not None:
        try:
            report = json.loads(build_report_art.content)
        except Exception as exc:
            return HandlerResult(
                status=NodeStatus.failed,
                artifacts=[],
                logs=f"Validator error parsing build_report JSON: {exc}",
            )

        build_attempted = report.get("build_attempted", False)
        tsc_exit_code = report.get("tsc_exit_code", 1)
        type_errors = report.get("type_errors", 0)
        compiled_ok = report.get("compiled_ok", False)

        passed = bool(build_attempted and tsc_exit_code == 0 and compiled_ok and type_errors == 0)
        failures = []
        if not build_attempted:
            failures.append("ts_build_container_failed")
        if tsc_exit_code != 0 or type_errors > 0 or not compiled_ok:
            failures.append(f"tsc_type_errors ({type_errors} error(s), exit_code: {tsc_exit_code})")

        verdict_payload = {
            "passed": passed,
            "failures": failures,
        }
        artifact_spec = ArtifactSpec(
            kind="verdict",
            filename="verdict.json",
            content=json.dumps(verdict_payload, indent=2),
            content_type="application/json",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"BuildValidator verdict: passed={passed}, failures={failures}",
            meta={"passed": passed, "failures": failures},
        )

    # TestValidator branch
    if test_report_art is not None:
        try:
            report = json.loads(test_report_art.content)
        except Exception as exc:
            return HandlerResult(
                status=NodeStatus.failed,
                artifacts=[],
                logs=f"Validator error parsing test_report JSON: {exc}",
            )

        service_booted = report.get("service_booted", False)
        passed_count = report.get("passed", 0)
        failed_count = report.get("failed", 0)

        passed = bool(service_booted and failed_count == 0 and passed_count > 0)
        failures = []
        if not service_booted:
            failures.append("service_boot_failed")
        if failed_count > 0:
            failures.append(f"{failed_count}_tests_failed")
        if service_booted and passed_count == 0:
            failures.append("zero_tests_passed")

        verdict_payload = {
            "passed": passed,
            "failures": failures,
        }
        artifact_spec = ArtifactSpec(
            kind="verdict",
            filename="verdict.json",
            content=json.dumps(verdict_payload, indent=2),
            content_type="application/json",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"TestValidator verdict: passed={passed}, failures={failures}",
            meta={"passed": passed, "failures": failures},
        )

    # Legacy Phase 0 backward compatibility for stdout artifacts
    if exec_report_art is None and stdout_art is not None:
        try:
            data = json.loads(stdout_art.content)
            exit_code = data.get("exit_code")
        except Exception as exc:
            return HandlerResult(
                status=NodeStatus.failed,
                artifacts=[],
                logs=f"Validator error parsing stdout JSON: {exc}",
            )
        verdict = "pass" if exit_code == 0 else "fail"
        artifact_spec = ArtifactSpec(
            kind="verdict",
            filename=f"verdict_{node.name}.txt",
            content=verdict,
            content_type="text/plain",
        )
        return HandlerResult(
            status=NodeStatus.completed,
            artifacts=[artifact_spec],
            logs=f"Validator checked stdout: exit_code={exit_code} -> verdict={verdict}",
            meta={"verdict": verdict, "exit_code": exit_code},
        )

    if exec_report_art is None:
        return HandlerResult(
            status=NodeStatus.failed,
            artifacts=[],
            logs="Validator error: missing required upstream 'execution_report' or 'test_report' artifact.",
        )

    try:
        report = json.loads(exec_report_art.content)
    except Exception as exc:
        return HandlerResult(
            status=NodeStatus.failed,
            artifacts=[],
            logs=f"Validator error parsing execution_report JSON: {exc}",
        )

    build_success = report.get("build_success", False)
    container_started = report.get("container_started", False)
    health_ok = report.get("health_ok", False)
    health_status_code = report.get("health_status_code")

    passed = build_success and container_started and health_ok and (health_status_code == 200)

    failures = []
    if not build_success:
        failures.append("build_failed")
    if not container_started:
        failures.append("container_failed_to_start")
    if not health_ok or health_status_code != 200:
        failures.append(f"health_check_failed (status: {health_status_code})")

    verdict_payload = {
        "passed": passed,
        "failures": failures,
    }

    artifact_spec = ArtifactSpec(
        kind="verdict",
        filename="verdict.json",
        content=json.dumps(verdict_payload, indent=2),
        content_type="application/json",
    )

    log_msg = f"Validator verdict: passed={passed}, failures={failures}"

    return HandlerResult(
        status=NodeStatus.completed,
        artifacts=[artifact_spec],
        logs=log_msg,
        meta={"passed": passed, "failures": failures},
    )
