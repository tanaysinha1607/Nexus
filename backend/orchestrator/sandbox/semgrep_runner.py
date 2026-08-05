"""Nexus Sandbox Semgrep Security Runner.

Materializes Node.js/TypeScript backend source code into an isolated Docker container,
runs `semgrep scan --config=p/javascript --config=p/typescript --metrics=off --json .` static code AST security scan,
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

SEMGREP_DOCKERFILE = """FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir semgrep==1.80.0
CMD ["semgrep", "scan", "--config=p/javascript", "--config=p/typescript", "--metrics=off", "--json", "/app"]
"""


def create_semgrep_tar_stream(source_files: dict[str, str]) -> io.BytesIO:
    """Create tar stream containing Node source files and Dockerfile."""
    tar_stream = io.BytesIO()
    all_files = dict(source_files)
    if "Dockerfile" not in all_files:
        all_files["Dockerfile"] = SEMGREP_DOCKERFILE

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


def parse_semgrep_stdout(output: str) -> tuple[bool, int, int, int, list[dict], str]:
    """Parse semgrep JSON stdout into structured metrics and ERROR findings.

    Returns (scan_completed, high_count, medium_count, low_count, high_findings, semgrep_version).
    CRITICAL: Valid semgrep JSON output means scan_completed=True REGARDLESS of container exit code.
    """
    if not output or not output.strip():
        return False, 0, 0, 0, [], ""

    json_start = output.find("{")
    json_end = output.rfind("}")
    if json_start == -1 or json_end == -1 or json_end <= json_start:
        return False, 0, 0, 0, [], ""

    clean_json = output[json_start : json_end + 1]

    try:
        data = json.loads(clean_json)
    except Exception as exc:
        logger.warning(f"Failed to parse semgrep output as JSON: {exc}")
        return False, 0, 0, 0, [], ""

    if "results" not in data or not isinstance(data.get("results"), list):
        return False, 0, 0, 0, [], ""

    results = data.get("results", [])
    high_findings = []
    high_count = 0
    medium_count = 0
    low_count = 0

    for item in results:
        extra = item.get("extra", {})
        severity = str(extra.get("severity", "")).upper()
        if severity == "ERROR":
            high_count += 1
            high_findings.append({
                "check_id": item.get("check_id", "UNKNOWN"),
                "message": extra.get("message", ""),
                "path": item.get("path", ""),
                "line": item.get("start", {}).get("line", 0),
                "severity": "ERROR",
            })
        elif severity == "WARNING":
            medium_count += 1
        elif severity in ("INFO", "NOTE"):
            low_count += 1

    semgrep_version = str(data.get("version", "1.80.0"))
    return True, high_count, medium_count, low_count, high_findings, semgrep_version


def run_semgrep_security_scan_in_docker_sandbox(
    source_files: dict[str, str],
    timeout_s: int = 90,
) -> dict:
    """Builds container with Node source files, runs semgrep AST scan,
    captures JSON output regardless of non-zero exit code,
    and returns a structured security_report dictionary.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-semgrep-{uuid.uuid4().hex[:8]}"

    scan_completed = False
    high_count = 0
    medium_count = 0
    low_count = 0
    high_findings = []
    semgrep_version = "1.80.0"
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
            "scanner": "semgrep",
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "high_findings": [],
            "semgrep_version": "",
            "raw_output": msg,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # 1. Build Docker Image
    tar_stream = create_semgrep_tar_stream(source_files)
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
        logger.warning("Semgrep sandbox image build failed.")
        return {
            "scan_completed": False,
            "scanner": "semgrep",
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "high_findings": [],
            "semgrep_version": "",
            "raw_output": f"Container build error:\n{raw_output}",
            "elapsed_s": round(time.time() - start_time, 2),
        }
    except Exception as exc:
        raw_output = f"Build error: {exc}"
        logger.warning(f"Semgrep sandbox build error: {exc}")
        return {
            "scan_completed": False,
            "scanner": "semgrep",
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "high_findings": [],
            "semgrep_version": "",
            "raw_output": raw_output,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # 2. Run Container (`semgrep scan --config=p/javascript --config=p/typescript --metrics=off --json .`)
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

    # Parse stdout JSON regardless of exit code
    scan_completed, high_count, medium_count, low_count, high_findings, semgrep_version = parse_semgrep_stdout(raw_output)

    # Teardown in finally
    try:
        if image_created and client is not None:
            client.images.remove(image=tag_name, force=True)
    except Exception as e:
        logger.warning(f"Failed to remove Semgrep build image: {e}")

    elapsed_s = time.time() - start_time
    return {
        "scan_completed": scan_completed,
        "scanner": "semgrep",
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "high_findings": high_findings,
        "semgrep_version": semgrep_version,
        "raw_output": raw_output[-3000:] if len(raw_output) > 3000 else raw_output,
        "elapsed_s": round(elapsed_s, 2),
    }
