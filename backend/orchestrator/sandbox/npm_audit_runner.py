"""Nexus Sandbox NPM Audit Security Runner.

Materializes Node.js backend package files into an isolated Docker container,
runs `npm audit --json` dependency security scan,
captures JSON stdout regardless of container exit code,
and guarantees container/image teardown.
"""

import io
import json
import logging
import tarfile
import time
import uuid
import docker

logger = logging.getLogger(__name__)

NPM_AUDIT_DOCKERFILE = """FROM node:20-slim
WORKDIR /app
COPY . /app
CMD ["npm", "audit", "--json"]
"""


def create_npm_audit_tar_stream(source_files: dict[str, str]) -> io.BytesIO:
    """Create tar stream containing Node source/package files and Dockerfile."""
    tar_stream = io.BytesIO()
    all_files = dict(source_files)
    if "Dockerfile" not in all_files:
        all_files["Dockerfile"] = NPM_AUDIT_DOCKERFILE

    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        for fname, fcontent in all_files.items():
            clean_name = fname.strip().lstrip("./").lstrip("/")
            content_bytes = fcontent.encode("utf-8")
            ti = tarfile.TarInfo(name=clean_name)
            ti.size = len(content_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(content_bytes))

    tar_stream.seek(0)
    return tar_stream


def parse_npm_audit_stdout(output: str) -> tuple[bool, int, int, int, int, list[dict], str]:
    """Parse npm audit JSON stdout into structured metrics and high/critical findings.

    Returns (scan_completed, high_count, critical_count, moderate_count, low_count, high_findings, npm_audit_version).
    """
    if not output or not output.strip():
        return False, 0, 0, 0, 0, [], ""

    json_start = output.find("{")
    json_end = output.rfind("}")
    if json_start == -1 or json_end == -1 or json_end <= json_start:
        return False, 0, 0, 0, 0, [], ""

    clean_json = output[json_start : json_end + 1]

    try:
        data = json.loads(clean_json)
    except Exception as exc:
        logger.warning(f"Failed to parse npm audit output as JSON: {exc}")
        return False, 0, 0, 0, 0, [], ""

    vulnerabilities = data.get("vulnerabilities", {})
    metadata_vulns = data.get("metadata", {}).get("vulnerabilities", {})

    high_count = metadata_vulns.get("high", 0)
    critical_count = metadata_vulns.get("critical", 0)
    moderate_count = metadata_vulns.get("moderate", 0)
    low_count = metadata_vulns.get("low", 0)

    high_findings = []
    if isinstance(vulnerabilities, dict):
        for pkg_name, info in vulnerabilities.items():
            severity = str(info.get("severity", "")).lower()
            if severity in ("high", "critical"):
                high_findings.append({
                    "package": pkg_name,
                    "severity": severity,
                    "name": info.get("name", pkg_name),
                    "range": info.get("range", ""),
                    "title": info.get("title", f"Vulnerable dependency {pkg_name}"),
                    "url": info.get("url", ""),
                })
                if severity == "high" and high_count == 0:
                    high_count += 1
                elif severity == "critical" and critical_count == 0:
                    critical_count += 1

    audit_version = str(data.get("auditReportVersion", 2))
    return True, high_count, critical_count, moderate_count, low_count, high_findings, audit_version


def run_npm_audit_scan_in_docker_sandbox(
    source_files: dict[str, str],
    timeout_s: int = 60,
) -> dict:
    """Builds container with Node package files, runs `npm audit --json`,
    captures JSON stdout regardless of exit code (npm audit exits 1 when vulns exist),
    and returns a structured security_report dictionary.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-npm-audit-{uuid.uuid4().hex[:8]}"

    scan_completed = False
    high_count = 0
    critical_count = 0
    moderate_count = 0
    low_count = 0
    high_findings = []
    npm_audit_version = "2"
    raw_output = ""

    client = None
    image_created = False

    try:
        client = docker.from_env()
    except Exception as exc:
        msg = f"Failed to connect to Docker daemon: {exc}"
        logger.error(msg)
        return {
            "scan_completed": False,
            "high_count": 0,
            "critical_count": 0,
            "moderate_count": 0,
            "low_count": 0,
            "high_findings": [],
            "npm_audit_version": "",
            "raw_output": msg,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # Build image
    tar_stream = create_npm_audit_tar_stream(source_files)
    build_logs_list = []

    try:
        image, logs_gen = client.images.build(
            fileobj=tar_stream,
            custom_context=True,
            tag=tag_name,
            rm=True,
            forcerm=True,
        )
        image_created = True
    except docker.errors.BuildError as be:
        for chunk in be.build_log:
            if "stream" in chunk:
                build_logs_list.append(chunk["stream"].strip())
        raw_output = "\n".join(build_logs_list[-30:]) if build_logs_list else str(be)
        logger.warning("NPM audit sandbox image build failed.")
        return {
            "scan_completed": False,
            "high_count": 0,
            "critical_count": 0,
            "moderate_count": 0,
            "low_count": 0,
            "high_findings": [],
            "npm_audit_version": "",
            "raw_output": f"Container build error:\n{raw_output}",
            "elapsed_s": round(time.time() - start_time, 2),
        }
    except Exception as exc:
        raw_output = f"Build error: {exc}"
        logger.warning(f"NPM audit sandbox build error: {exc}")
        return {
            "scan_completed": False,
            "high_count": 0,
            "critical_count": 0,
            "moderate_count": 0,
            "low_count": 0,
            "high_findings": [],
            "npm_audit_version": "",
            "raw_output": raw_output,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # Run container (`npm audit --json`)
    try:
        container_out = client.containers.run(
            tag_name,
            detach=False,
            mem_limit="512m",
            nano_cpus=1000000000,
            pids_limit=100,
        )
        raw_output = container_out.decode("utf-8", errors="replace") if isinstance(container_out, bytes) else str(container_out)
    except docker.errors.ContainerError as ce:
        stdout_bytes = b""
        if hasattr(ce, "container") and ce.container:
            try:
                stdout_bytes = ce.container.logs(stdout=True, stderr=False)
            except Exception:
                stdout_bytes = b""
        if not stdout_bytes:
            stdout_bytes = getattr(ce, "stderr", None) or b""

        if isinstance(stdout_bytes, bytes):
            raw_output = stdout_bytes.decode("utf-8", errors="replace")
        else:
            raw_output = str(stdout_bytes)

        if not raw_output and str(ce):
            raw_output = str(ce)
    except Exception as exc:
        raw_output = f"Execution error: {exc}"

    scan_completed, high_count, critical_count, moderate_count, low_count, high_findings, npm_audit_version = parse_npm_audit_stdout(raw_output)

    try:
        if image_created and client is not None:
            client.images.remove(image=tag_name, force=True)
    except Exception as e:
        logger.warning(f"Failed to remove NPM audit build image: {e}")

    elapsed_s = time.time() - start_time
    return {
        "scan_completed": scan_completed,
        "scanner": "npm_audit",
        "high_count": high_count,
        "critical_count": critical_count,
        "moderate_count": moderate_count,
        "low_count": low_count,
        "high_findings": high_findings,
        "npm_audit_version": npm_audit_version,
        "raw_output": raw_output[-3000:] if len(raw_output) > 3000 else raw_output,
        "elapsed_s": round(elapsed_s, 2),
    }
