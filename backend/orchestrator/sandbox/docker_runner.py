import io
import logging
import tarfile
import time
import uuid
import docker

logger = logging.getLogger(__name__)

DEFAULT_PYTHON_DOCKERFILE = """FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
CMD ["sh", "-c", "python -c 'import main' && uvicorn main:app --host 0.0.0.0 --port 8000"]
"""

DEFAULT_NODE_DOCKERFILE = """FROM node:20-slim
WORKDIR /app
COPY . /app
RUN npm install
EXPOSE 8000
CMD ["npm", "start"]
"""


def create_tar_stream(files: dict[str, str], is_node: bool = False) -> io.BytesIO:
    """Create an in-memory tarball stream containing all source files and a Dockerfile."""
    tar_stream = io.BytesIO()
    files_to_pack = dict(files)
    if "Dockerfile" not in files_to_pack:
        files_to_pack["Dockerfile"] = DEFAULT_NODE_DOCKERFILE if is_node else DEFAULT_PYTHON_DOCKERFILE

    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        for fname, fcontent in files_to_pack.items():
            clean_name = fname.strip().lstrip("./").lstrip("/")
            content_bytes = fcontent.encode("utf-8")
            ti = tarfile.TarInfo(name=clean_name)
            ti.size = len(content_bytes)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(content_bytes))

    tar_stream.seek(0)
    return tar_stream


def run_code_in_docker_sandbox(
    source_files: dict[str, str],
    timeout_s: int = 45,
    manifest: dict | None = None,
) -> dict:
    """Builds and runs source code in an isolated Docker container sandbox.
    
    Dispatches toolchain based on build_manifest language ('python' vs 'node').
    Emits an execution report dictionary. Guaranteed teardown of container & image.
    """
    start_time = time.time()
    tag_name = f"nexus-sandbox-{uuid.uuid4().hex[:8]}"
    
    is_node = False
    if manifest and isinstance(manifest, dict) and manifest.get("language") == "node":
        is_node = True
    elif "package.json" in source_files:
        is_node = True

    build_success = False
    build_logs_tail = ""
    container_started = False
    health_status_code = None
    health_ok = False
    container_logs_tail = ""
    
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
            "health_status_code": None,
            "health_ok": False,
            "elapsed_s": round(time.time() - start_time, 2),
            "container_logs_tail": "",
        }

    # 1. Build Image
    tar_stream = create_tar_stream(source_files, is_node=is_node)
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
        logger.warning("Sandbox image build failed.")
    except Exception as exc:
        build_logs_tail = f"Build error: {exc}"
        logger.warning(f"Sandbox build encountered error: {exc}")

    # 2. Run Container if build succeeded
    if build_success:
        try:
            container = client.containers.run(
                tag_name,
                detach=True,
                mem_limit="512m",
                nano_cpus=1000000000,  # 1 CPU
                pids_limit=100,
                environment={"JWT_SECRET_KEY": "nexus-sandbox-test-secret"},
            )
            container_started = True

            # 3. Health Probe: poll /health in-container up to 15s
            probe_deadline = time.time() + 15.0
            if is_node:
                cmd = "node -e \"require('http').get('http://127.0.0.1:8000/health', (res) => process.exit(res.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))\""
            else:
                cmd = "python -c \"import urllib.request; res=urllib.request.urlopen('http://127.0.0.1:8000/health'); exit(0 if res.status==200 else 1)\""

            while time.time() < probe_deadline:
                time.sleep(1.0)
                container.reload()
                if container.status != "running":
                    break
                
                exec_res = container.exec_run(cmd)
                if exec_res.exit_code == 0:
                    health_status_code = 200
                    health_ok = True
                    break

        except Exception as exc:
            logger.warning(f"Container execution error: {exc}")

        if container is not None:
            try:
                raw_logs = container.logs(stdout=True, stderr=True)
                if isinstance(raw_logs, bytes):
                    raw_logs = raw_logs.decode("utf-8", errors="replace")
                lines = raw_logs.splitlines()
                container_logs_tail = "\n".join(lines[-100:])
                if len(container_logs_tail) > 4000:
                    container_logs_tail = container_logs_tail[-4000:]
            except Exception as e:
                container_logs_tail = f"Failed to retrieve container logs: {e}"

    # Teardown in finally block
    try:
        if container is not None:
            container.remove(force=True)
    except Exception as e:
        logger.warning(f"Failed to remove sandbox container: {e}")

    try:
        if image_created and client is not None:
            client.images.remove(image=tag_name, force=True)
    except Exception as e:
        logger.warning(f"Failed to remove sandbox image: {e}")

    elapsed_s = time.time() - start_time
    return {
        "build_success": build_success,
        "build_logs_tail": build_logs_tail,
        "container_started": container_started,
        "health_status_code": health_status_code,
        "health_ok": health_ok,
        "elapsed_s": round(elapsed_s, 2),
        "container_logs_tail": container_logs_tail,
    }
