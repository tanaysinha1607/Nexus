"""Output parsing convention for Nexus agent completion text.

CONVENTION:
  Agents emit output files prefixed with explicit header blocks:

  === FILE: filename.md ===
  ```markdown
  ... content ...
  ```

PARSER CONTRACT:
  - Extracts (filename, content) pairs in declared header order.
  - Maps filename -> declared OutputSpec to determine artifact kind.
  - Files not declared in outputs are IGNORED and logged.
  - Missing REQUIRED outputs cause a parse failure (is_valid = False).
  - Parse failure returns a single raw_response artifact spec for debugging.
"""

import logging
import re
from typing import Any

from orchestrator.agents.roles import OutputSpec
from orchestrator.handlers import ArtifactSpec

logger = logging.getLogger(__name__)

# Matches === FILE: filename.ext === followed by optional fenced code block or raw text
FILE_HEADER_PATTERN = re.compile(r"===\s*FILE:\s*([^\s=]+)\s*===", re.IGNORECASE)


def parse_agent_output(
    raw_text: str,
    declared_outputs: list[OutputSpec],
    role: Any | None = None,
) -> tuple[bool, list[ArtifactSpec], str]:
    """Parse raw LLM completion text into ArtifactSpec list according to declared outputs or role rules.

    Args:
        raw_text: Raw string returned by the LLM.
        declared_outputs: List of OutputSpec declared on the AgentRole.
        role: Optional AgentRole instance for role-specific parsing logic.

    Returns:
        tuple[bool, list[ArtifactSpec], str]:
            - is_valid: True if outputs were successfully parsed and validated.
            - artifact_specs: List of parsed ArtifactSpec objects (or raw_response on failure).
            - log_reason: Explanation summary of parsing results.
    """
    if not raw_text or not raw_text.strip():
        logger.warning("Agent output is empty")
        return (
            False,
            [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text or "")],
            "agent output unparseable: empty response",
        )

    header_matches = list(FILE_HEADER_PATTERN.finditer(raw_text))
    if not header_matches:
        # Fallback: if single declared JSON output (e.g. api_contract.json), attempt extract_json
        if len(declared_outputs) == 1 and declared_outputs[0].filename.endswith(".json"):
            try:
                from orchestrator.agents.json_extract import extract_json

                clean_json = extract_json(raw_text)
                produced_specs = [
                    ArtifactSpec(
                        kind=declared_outputs[0].kind,
                        filename=declared_outputs[0].filename,
                        content=clean_json,
                    )
                ]
                logger.info("Successfully extracted single JSON output via fallback parser")
                # Proceed to json validation block below
            except Exception as e:
                logger.warning(f"No === FILE: filename === headers found in agent output and JSON fallback failed: {e}")
                return (
                    False,
                    [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                    "agent output unparseable: no === FILE: filename === headers found",
                )
        else:
            logger.warning("No === FILE: filename === headers found in agent output")
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                "agent output unparseable: no === FILE: filename === headers found",
            )
    else:
        extracted_files: dict[str, str] = {}

    for i, match in enumerate(header_matches):
        filename = match.group(1).strip()
        start_pos = match.end()
        end_pos = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(raw_text)

        block_text = raw_text[start_pos:end_pos].strip()

        # Strip opening ```lang line if present
        if block_text.startswith("```"):
            first_line_end = block_text.find("\n")
            if first_line_end != -1:
                block_text = block_text[first_line_end + 1 :]
            else:
                block_text = block_text[3:]

        # Strip trailing ``` line if present
        block_text = block_text.strip()
        if block_text.endswith("```"):
            block_text = block_text[:-3]

        extracted_files[filename] = block_text.strip()

    # Special multi-artifact source code mode (e.g. backend_engineer)
    if role is not None and getattr(role, "accept_any_file", False):
        # 1. Entrypoint check
        if "main.py" not in extracted_files:
            log_reason = "missing entrypoint main.py"
            logger.warning(log_reason)
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                log_reason,
            )

        # 2. Python syntax compile check
        main_content = extracted_files["main.py"]
        try:
            compile(main_content, "main.py", "exec")
        except SyntaxError as exc:
            detail = f"line {exc.lineno}: {exc.msg}" if exc.lineno else str(exc)
            log_reason = f"generated main.py has syntax error: {detail}"
            logger.warning(log_reason)
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                log_reason,
            )

        # 3. Requirements check
        if "requirements.txt" not in extracted_files or not extracted_files["requirements.txt"].strip():
            log_reason = "missing or empty requirements.txt"
            logger.warning(log_reason)
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                log_reason,
            )

        # 4. Forbidden external dependencies check
        forbidden_targets = {"sqlalchemy", "alembic", "psycopg", "asyncpg", "redis"}
        hits = set()
        for fname, fcontent in extracted_files.items():
            found = re.findall(r"\b(sqlalchemy|alembic|psycopg|asyncpg|redis)\b", fcontent, re.IGNORECASE)
            for match in found:
                hits.add(match.lower())

        if hits:
            sorted_hits = ", ".join(sorted(list(hits)))
            log_reason = f"generated code depends on external services: {sorted_hits}"
            logger.warning(log_reason)
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                log_reason,
            )

        # 5. AST Import vs requirements.txt consistency check
        import ast
        import sys

        stdlib_names = getattr(sys, "stdlib_module_names", set())
        module_to_pkg_map = {
            "cryptography": "cryptography",
            "jose": "python-jose",
            "fastapi": "fastapi",
            "uvicorn": "uvicorn",
            "pydantic": "pydantic",
            "httpx": "httpx",
            "jwt": "pyjwt",
        }

        req_content = extracted_files.get("requirements.txt", "")
        req_lines = [line.strip().lower() for line in req_content.splitlines() if line.strip() and not line.strip().startswith("#")]
        req_packages = set()
        for line in req_lines:
            pkg = re.split(r"[=<>]", line)[0].strip()
            pkg_base = pkg.split("[")[0].strip()
            req_packages.add(pkg_base)
            req_packages.add(pkg)

        imported_modules = set()
        uses_email_str = False

        for fname, fcontent in extracted_files.items():
            if not fname.endswith(".py"):
                continue
            try:
                tree = ast.parse(fcontent, filename=fname)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_module = alias.name.split(".")[0]
                        imported_modules.add(top_module)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_module = node.module.split(".")[0]
                        imported_modules.add(top_module)
                        if top_module == "pydantic":
                            for alias in node.names:
                                if alias.name == "EmailStr":
                                    uses_email_str = True

        third_party_modules = {m for m in imported_modules if m and m not in stdlib_names and m not in ("main", "app")}
        required_pkgs = set()
        for mod in third_party_modules:
            mapped = module_to_pkg_map.get(mod, mod)
            required_pkgs.add(mapped)

        if uses_email_str:
            required_pkgs.add("email-validator")

        missing_imports = []
        for pkg in sorted(list(required_pkgs)):
            if pkg == "email-validator":
                found = any("email-validator" in r or "email_validator" in r or "pydantic[email]" in r for r in req_packages)
            elif pkg == "python-jose":
                found = any("python-jose" in r or "jose" in r for r in req_packages)
            elif pkg == "pydantic":
                found = any("pydantic" in r or "fastapi" in r for r in req_packages)
            else:
                found = any(pkg == r or pkg in r or pkg.replace("-", "_") in r.replace("-", "_") for r in req_packages)

            if not found:
                missing_imports.append(pkg)

        if missing_imports:
            sorted_missing = ", ".join(sorted(missing_imports))
            log_reason = f"requirements.txt missing packages for imports: {sorted_missing}"
            logger.warning(log_reason)
            return (
                False,
                [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                log_reason,
            )

        # Build list of source_code artifacts
        produced_specs: list[ArtifactSpec] = []
        for fname, fcontent in extracted_files.items():
            produced_specs.append(
                ArtifactSpec(
                    kind="source_code",
                    filename=fname,
                    content=fcontent,
                )
            )

        return True, produced_specs, "agent output successfully parsed"

    # Standard declared output mapping mode
    output_map = {spec.filename: spec for spec in declared_outputs}
    produced_specs: list[ArtifactSpec] = []
    missing_required: list[str] = []

    # Check for missing required outputs
    for spec in declared_outputs:
        if spec.filename in extracted_files:
            content = extracted_files[spec.filename]
            produced_specs.append(
                ArtifactSpec(
                    kind=spec.kind,
                    filename=spec.filename,
                    content=content,
                )
            )
        elif spec.required:
            missing_required.append(spec.filename)

    # Log extra files not declared in outputs
    for filename in extracted_files:
        if filename not in output_map:
            logger.info(f"Ignoring undeclared output file in agent response: {filename}")

    if missing_required:
        log_reason = f"agent output unparseable: missing required file(s) {missing_required}"
        logger.warning(log_reason)
        return (
            False,
            [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
            log_reason,
        )

    # Validate api_contract artifacts if present
    import json
    from orchestrator.agents.json_extract import extract_json

    for i, spec in enumerate(produced_specs):
        if spec.kind == "api_contract" or spec.filename.endswith(".json"):
            # Attempt robust JSON extraction
            try:
                clean_json_str = extract_json(spec.content)
                contract_data = json.loads(clean_json_str)
                # Store cleaned JSON back into spec content
                produced_specs[i] = ArtifactSpec(
                    kind=spec.kind,
                    filename=spec.filename,
                    content=clean_json_str,
                )
            except Exception as e:
                # If block text failed, attempt extraction directly from full raw_text
                try:
                    clean_json_str = extract_json(raw_text)
                    contract_data = json.loads(clean_json_str)
                    produced_specs[i] = ArtifactSpec(
                        kind=spec.kind,
                        filename=spec.filename,
                        content=clean_json_str,
                    )
                except Exception as e2:
                    log_reason = "api_contract not valid JSON"
                    logger.warning(f"{log_reason}: {e2}")
                    return (
                        False,
                        [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                        log_reason,
                    )

            if not isinstance(contract_data, dict) or "endpoints" not in contract_data:
                log_reason = "api_contract not valid JSON"
                logger.warning(log_reason)
                return (
                    False,
                    [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                    log_reason,
                )

            endpoints = contract_data.get("endpoints")
            if not isinstance(endpoints, list) or len(endpoints) == 0:
                log_reason = "api_contract not valid JSON"
                logger.warning(log_reason)
                return (
                    False,
                    [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                    log_reason,
                )

            for ep in endpoints:
                if not isinstance(ep, dict):
                    log_reason = "api_contract not valid JSON"
                    logger.warning(log_reason)
                    return (
                        False,
                        [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                        log_reason,
                    )
                required_keys = {"method", "path", "request_schema", "response_schema"}
                if not required_keys.issubset(ep.keys()):
                    log_reason = "api_contract not valid JSON"
                    logger.warning(log_reason)
                    return (
                        False,
                        [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                        log_reason,
                    )

    # Validate review artifacts if present (senior_reviewer)
    for spec in produced_specs:
        if spec.kind == "review" or spec.filename == "review.md":
            match = re.search(r"REVIEW_VERDICT:\s*(approved|changes_requested)", spec.content, re.IGNORECASE)
            if not match:
                log_reason = "review verdict unparseable"
                logger.warning(log_reason)
                return (
                    False,
                    [ArtifactSpec(kind="raw_response", filename="raw_response.md", content=raw_text)],
                    log_reason,
                )

    return True, produced_specs, "agent output successfully parsed"
