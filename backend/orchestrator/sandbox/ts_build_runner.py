"""Nexus Sandbox TypeScript Build Runner.

Materializes TypeScript frontend client code into an isolated Docker container,
runs `tsc --noEmit --strict`, captures compilation type errors,
and guarantees container/image teardown.
"""

import io
import logging
import re
import tarfile
import time
import uuid
import docker

logger = logging.getLogger(__name__)

TS_BUILD_DOCKERFILE = """FROM node:20-slim
WORKDIR /app
COPY . /app
RUN npm install -g typescript@5.3.3
CMD ["tsc", "--noEmit"]
"""

DEFAULT_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "esModuleInterop": true
  },
  "include": ["**/*.ts"]
}
"""


def create_ts_tar_stream(frontend_files: dict[str, str]) -> io.BytesIO:
    """Create tar stream containing frontend TS files, tsconfig.json, and Dockerfile."""
    tar_stream = io.BytesIO()
    all_files = dict(frontend_files)
    if "tsconfig.json" not in all_files:
        all_files["tsconfig.json"] = DEFAULT_TSCONFIG
    if "Dockerfile" not in all_files:
        all_files["Dockerfile"] = TS_BUILD_DOCKERFILE

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


def parse_tsc_stdout(output: str) -> tuple[int, bool]:
    """Parse tsc stdout/stderr for error count and compiled_ok status.
    
    Returns (type_errors, compiled_ok).
    """
    if not output or not output.strip():
        return 0, True

    # Check for explicit tsc summary line: "Found 3 errors in 2 files." or "Found 1 error."
    summary_match = re.search(r"Found\s+(\d+)\s+error", output, re.IGNORECASE)
    if summary_match:
        type_errors = int(summary_match.group(1))
    else:
        # Fallback: count lines containing error TS
        error_lines = [line for line in output.splitlines() if "error TS" in line]
        type_errors = len(error_lines)

    compiled_ok = (type_errors == 0) and ("error TS" not in output)
    return type_errors, compiled_ok


def run_ts_build_in_docker_sandbox(
    frontend_files: dict[str, str],
    timeout_s: int = 60,
) -> dict:
    """Builds container with frontend code and tsconfig, runs `tsc --noEmit`,
    and returns a structured build_report dictionary.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-ts-{uuid.uuid4().hex[:8]}"

    build_attempted = False
    tsc_exit_code = 1
    type_errors = 0
    compiled_ok = False
    tsc_output_tail = ""

    client = None
    container = None
    image_created = False

    try:
        client = docker.from_env()
    except Exception as exc:
        msg = f"Failed to connect to Docker daemon: {exc}"
        logger.error(msg)
        return {
            "build_attempted": False,
            "tsc_exit_code": 1,
            "type_errors": 0,
            "compiled_ok": False,
            "tsc_output_tail": msg,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # 1. Build Docker Image
    tar_stream = create_ts_tar_stream(frontend_files)
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
        build_attempted = True
    except docker.errors.BuildError as be:
        for chunk in be.build_log:
            if "stream" in chunk:
                build_logs_list.append(chunk["stream"].strip())
        tsc_output_tail = "\n".join(build_logs_list[-30:]) if build_logs_list else str(be)
        logger.warning("TS sandbox image build failed.")
        return {
            "build_attempted": False,
            "tsc_exit_code": 1,
            "type_errors": 1,
            "compiled_ok": False,
            "tsc_output_tail": f"Container build error:\n{tsc_output_tail}",
            "elapsed_s": round(time.time() - start_time, 2),
        }
    except Exception as exc:
        tsc_output_tail = f"Build error: {exc}"
        logger.warning(f"TS sandbox build error: {exc}")
        return {
            "build_attempted": False,
            "tsc_exit_code": 1,
            "type_errors": 1,
            "compiled_ok": False,
            "tsc_output_tail": tsc_output_tail,
            "elapsed_s": round(time.time() - start_time, 2),
        }

    # 2. Run Container (`tsc --noEmit`)
    try:
        container = client.containers.run(
            tag_name,
            detach=False,
            mem_limit="512m",
            nano_cpus=1000000000,
            pids_limit=100,
        )
        # docker run returns bytes output when detach=False
        raw_out = container.decode("utf-8", errors="replace") if isinstance(container, bytes) else str(container)
        tsc_exit_code = 0
        tsc_output_tail = raw_out[-3000:] if len(raw_out) > 3000 else raw_out
        type_errors, compiled_ok = parse_tsc_stdout(raw_out)

    except docker.errors.ContainerError as ce:
        tsc_exit_code = ce.exit_status
        raw_out = ce.stderr.decode("utf-8", errors="replace") if isinstance(ce.stderr, bytes) else str(ce.stderr or ce)
        if not raw_out and str(ce):
            raw_out = str(ce)
        tsc_output_tail = raw_out[-3000:] if len(raw_out) > 3000 else raw_out
        type_errors, compiled_ok = parse_tsc_stdout(raw_out)
        compiled_ok = False

    except Exception as exc:
        tsc_exit_code = 1
        tsc_output_tail = f"Execution error: {exc}"
        type_errors = 1
        compiled_ok = False

    # Teardown in finally
    try:
        if image_created and client is not None:
            client.images.remove(image=tag_name, force=True)
    except Exception as e:
        logger.warning(f"Failed to remove TS build image: {e}")

    elapsed_s = time.time() - start_time
    return {
        "build_attempted": build_attempted,
        "tsc_exit_code": tsc_exit_code,
        "type_errors": type_errors,
        "compiled_ok": compiled_ok,
        "tsc_output_tail": tsc_output_tail,
        "elapsed_s": round(elapsed_s, 2),
    }
