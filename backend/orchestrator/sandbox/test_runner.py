"""Nexus Sandbox Contract Test Runner.

Materializes source code + test code into an isolated Docker container,
boots the service, runs black-box integration tests over HTTP (pytest for Python, npm test for Node),
captures execution metrics, and guarantees container/image teardown.
"""

import io
import logging
import re
import tarfile
import time
import uuid
import docker

logger = logging.getLogger(__name__)

PYTHON_TEST_RUNNER_DOCKERFILE = """FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt pytest httpx
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000"]
"""

NODE_TEST_RUNNER_DOCKERFILE = """FROM node:20-slim
WORKDIR /app
COPY . /app
RUN npm install
CMD ["npm", "start"]
"""


def create_test_tar_stream(source_files: dict[str, str], test_files: dict[str, str], is_node: bool = False) -> io.BytesIO:
    """Create tar stream containing both source code and test files."""
    tar_stream = io.BytesIO()
    all_files = dict(source_files)
    all_files.update(test_files)
    if "Dockerfile" not in all_files:
        all_files["Dockerfile"] = NODE_TEST_RUNNER_DOCKERFILE if is_node else PYTHON_TEST_RUNNER_DOCKERFILE

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


def parse_pytest_stdout(output: str) -> tuple[int, int, int]:
    """Parse plain pytest stdout for collected, passed, and failed counts."""
    tests_collected = 0
    passed = 0
    failed = 0

    col_match = re.search(r"collected\s+(\d+)\s+item", output, re.IGNORECASE)
    if col_match:
        tests_collected = int(col_match.group(1))

    summary_match = re.search(r"=+\s+(.*?)\s+in\s+[\d\.]+s\s+=+", output)
    if summary_match:
        summary_str = summary_match.group(1)
        p_match = re.search(r"(\d+)\s+passed", summary_str)
        if p_match:
            passed = int(p_match.group(1))
        f_match = re.search(r"(\d+)\s+failed", summary_str)
        if f_match:
            failed = int(f_match.group(1))
    else:
        p_match = re.search(r"(\d+)\s+passed", output)
        if p_match:
            passed = int(p_match.group(1))
        f_match = re.search(r"(\d+)\s+failed", output)
        if f_match:
            failed = int(f_match.group(1))

    if tests_collected == 0:
        tests_collected = passed + failed

    return tests_collected, passed, failed


def parse_node_test_stdout(output: str) -> tuple[int, int, int]:
    """Parse Node test output (e.g. node --test or jest stdout) for metrics."""
    passed = 0
    failed = 0
    tests_collected = 0

    pass_match = re.search(r"pass(?:ed)?\s+(\d+)", output, re.IGNORECASE)

    if pass_match:
        passed = int(pass_match.group(1))

    fail_match = re.search(r"fail(?:ed)?\s+(\d+)", output, re.IGNORECASE)
    if fail_match:
        failed = int(fail_match.group(1))

    # Match node --test output format: "ℹ tests 3" or "ℹ pass 3"
    total_match = re.search(r"tests\s+(\d+)", output, re.IGNORECASE)
    if total_match:
        tests_collected = int(total_match.group(1))

    # Fallback OK heuristic if output contains "ok" or exit 0 and no explicit failure
    if passed == 0 and failed == 0:
        if "ok " in output or "pass" in output.lower() or "100%" in output:
            passed = 1
            tests_collected = 1
        elif "error" in output.lower() or "fail" in output.lower() or "no test specified" in output.lower() or "missing script" in output.lower() or "err!" in output.lower():
            failed = 1
            tests_collected = 1

    if tests_collected == 0:
        tests_collected = passed + failed

    return tests_collected, passed, failed


def run_contract_tests_in_docker_sandbox(
    source_files: dict[str, str],
    test_files: dict[str, str],
    timeout_s: int = 60,
    manifest: dict | None = None,
) -> dict:
    """Builds container with app + test files, boots service,
    runs contract tests against http://127.0.0.1:8000 inside container,
    and returns a structured test_report dictionary.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-test-{uuid.uuid4().hex[:8]}"

    is_node = False
    if manifest and isinstance(manifest, dict) and manifest.get("language") == "node":
        is_node = True
    elif any(f.endswith((".js", ".ts", ".json")) for f in test_files.keys()):
        is_node = True

    build_success = False
    build_logs_tail = ""
    container_started = False
    service_booted = False
    tests_collected = 0
    passed = 0
    failed = 0
    exit_code = 1
    pytest_output_tail = ""

    client = None
    container = None
    image_created = False

    try:
        client = docker.from_env()
    except Exception as exc:
        msg = f"Failed to connect to Docker daemon: {exc}"
        logger.error(msg)
        return {
            "build_success": False,
            "build_logs_tail": msg,
            "container_started": False,
            "service_booted": False,
            "tests_collected": 0,
            "passed": 0,
            "failed": 0,
            "exit_code": 1,
            "pytest_output_tail": msg,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # 1. Build Image
    tar_stream = create_test_tar_stream(source_files, test_files, is_node=is_node)
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
        for chunk in logs_gen:
            if "stream" in chunk:
                build_logs_list.append(chunk["stream"].strip())
        build_success = True
        build_logs_tail = "\n".join(build_logs_list[-30:]) if build_logs_list else "Build succeeded."
    except docker.errors.BuildError as be:
        for chunk in be.build_log:
            if "stream" in chunk:
                build_logs_list.append(chunk["stream"].strip())
        build_logs_tail = "\n".join(build_logs_list[-30:]) if build_logs_list else str(be)
        logger.warning("Test sandbox image build failed.")
    except Exception as exc:
        build_logs_tail = f"Build error: {exc}"
        logger.warning(f"Test sandbox build error: {exc}")

    # 2. Run Container & Execute Tests
    if build_success:
        try:
            container = client.containers.run(
                tag_name,
                detach=True,
                mem_limit="512m",
                nano_cpus=1000000000,
                pids_limit=100,
                environment={"JWT_SECRET_KEY": "nexus-sandbox-test-secret"},
            )
            container_started = True

            # Poll /health in-container until 200 OK (max 15s)
            probe_deadline = time.time() + 15.0
            if is_node:
                cmd_probe = "node -e \"require('http').get('http://127.0.0.1:8000/health', (res) => process.exit(res.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))\""
            else:
                cmd_probe = "python -c \"import urllib.request; res=urllib.request.urlopen('http://127.0.0.1:8000/health'); exit(0 if res.status==200 else 1)\""

            while time.time() < probe_deadline:
                time.sleep(1.0)
                container.reload()
                if container.status != "running":
                    break

                exec_probe = container.exec_run(cmd_probe)
                if exec_probe.exit_code == 0:
                    service_booted = True
                    break

            if service_booted:
                test_file_name = list(test_files.keys())[0] if test_files else ("test_api.js" if is_node else "test_api.py")

                if is_node:
                    # Run node test runner inside container
                    cmd_test = f"node --test {test_file_name}"
                    exec_test = container.exec_run(cmd_test)
                    exit_code = exec_test.exit_code
                    raw_out = exec_test.output.decode("utf-8", errors="replace") if isinstance(exec_test.output, bytes) else str(exec_test.output)

                    # Fallback to npm test if node --test exit non-zero or missing
                    if exit_code != 0 and "no such file" in raw_out.lower():
                        exec_npm = container.exec_run("npm test")
                        exit_code = exec_npm.exit_code
                        raw_out = exec_npm.output.decode("utf-8", errors="replace") if isinstance(exec_npm.output, bytes) else str(exec_npm.output)

                    pytest_output_tail = raw_out[-3000:] if len(raw_out) > 3000 else raw_out
                    tests_collected, passed, failed = parse_node_test_stdout(raw_out)
                    if "no test specified" in raw_out.lower():
                        tests_collected = 0
                        passed = 0
                        failed = 1
                else:
                    # Run pytest inside running container
                    cmd_pytest = f"pytest -v {test_file_name}"
                    exec_pytest = container.exec_run(cmd_pytest)
                    exit_code = exec_pytest.exit_code
                    raw_pytest_out = exec_pytest.output.decode("utf-8", errors="replace") if isinstance(exec_pytest.output, bytes) else str(exec_pytest.output)

                    pytest_output_tail = raw_pytest_out[-3000:] if len(raw_pytest_out) > 3000 else raw_pytest_out
                    tests_collected, passed, failed = parse_pytest_stdout(raw_pytest_out)

            else:
                pytest_output_tail = "Service failed to boot (health check timed out or container crashed)."

        except Exception as exc:
            logger.warning(f"Test container execution error: {exc}")
            pytest_output_tail = f"Container execution error: {exc}"

    # Teardown in finally
    try:
        if container is not None:
            container.remove(force=True)
    except Exception as e:
        logger.warning(f"Failed to remove test container: {e}")

    try:
        if image_created and client is not None:
            client.images.remove(image=tag_name, force=True)
    except Exception as e:
        logger.warning(f"Failed to remove test image: {e}")

    elapsed_s = time.time() - start_time
    return {
        "build_success": build_success,
        "build_logs_tail": build_logs_tail,
        "container_started": container_started,
        "service_booted": service_booted,
        "tests_collected": tests_collected,
        "passed": passed,
        "failed": failed,
        "exit_code": exit_code,
        "pytest_output_tail": pytest_output_tail,
        "elapsed_s": round(elapsed_s, 2),
    }
