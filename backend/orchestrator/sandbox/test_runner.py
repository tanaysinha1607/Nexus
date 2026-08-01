"""Nexus Sandbox Contract Test Runner.

Materializes source code + test code into an isolated Docker container,
boots the service, runs black-box pytest integration tests over HTTP,
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

TEST_RUNNER_DOCKERFILE = """FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt pytest httpx
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000"]
"""


def create_test_tar_stream(source_files: dict[str, str], test_files: dict[str, str]) -> io.BytesIO:
    """Create tar stream containing both source code and test files."""
    tar_stream = io.BytesIO()
    all_files = dict(source_files)
    all_files.update(test_files)
    if "Dockerfile" not in all_files:
        all_files["Dockerfile"] = TEST_RUNNER_DOCKERFILE

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
    """Parse plain pytest stdout for collected, passed, and failed counts.
    
    Returns (tests_collected, passed, failed).
    """
    tests_collected = 0
    passed = 0
    failed = 0

    col_match = re.search(r"collected\s+(\d+)\s+item", output, re.IGNORECASE)
    if col_match:
        tests_collected = int(col_match.group(1))

    # Match summary lines like: "3 passed, 1 failed in 0.12s" or "2 passed in 0.05s" or "1 failed in 0.10s"
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
        # Fallback regex search on whole output
        p_match = re.search(r"(\d+)\s+passed", output)
        if p_match:
            passed = int(p_match.group(1))
        f_match = re.search(r"(\d+)\s+failed", output)
        if f_match:
            failed = int(f_match.group(1))

    if tests_collected == 0:
        tests_collected = passed + failed

    return tests_collected, passed, failed


def run_contract_tests_in_docker_sandbox(
    source_files: dict[str, str],
    test_files: dict[str, str],
    timeout_s: int = 60,
) -> dict:
    """Builds container with app + test files, boots uvicorn in background,
    runs `pytest -q test_api.py` against http://127.0.0.1:8000 inside container,
    and returns a structured test_report dictionary.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-test-{uuid.uuid4().hex[:8]}"

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
    tar_stream = create_test_tar_stream(source_files, test_files)
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
                # Determine test file name (default test_api.py)
                test_file_name = "test_api.py"
                if test_files:
                    test_file_name = list(test_files.keys())[0]

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
