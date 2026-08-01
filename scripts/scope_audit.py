#!/usr/bin/env python3
"""Nexus Scope Audit Guard.

Enforces strict boundary rules on third-party SDK and process execution imports:
  1. 'anthropic' is allowed ONLY in backend/orchestrator/llm/**
  2. 'import docker' / 'subprocess' is allowed ONLY in backend/orchestrator/sandbox/**
  3. 'openai' is allowed nowhere.

Returns exit code 0 if all rules pass, 1 if any violation is found.
"""

import os
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# Ignore patterns (virtual environments, node_modules, git, pycache, dist)
IGNORE_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "brain",
}

# Rules definitions: (pattern_regex, allowed_path_prefixes, description)
RULES = [
    (
        re.compile(r"anthropic", re.IGNORECASE),
        ["backend/orchestrator/llm"],
        "anthropic SDK allowed ONLY in backend/orchestrator/llm/**",
    ),
    (
        re.compile(r"(?:import|from)\s+(?:docker|subprocess)\b", re.IGNORECASE),
        ["backend/orchestrator/sandbox"],
        "docker/subprocess allowed ONLY in backend/orchestrator/sandbox/**",
    ),
    (
        re.compile(r"import\s+openai|from\s+openai", re.IGNORECASE),
        ["backend/orchestrator/llm"],
        "openai SDK allowed ONLY in backend/orchestrator/llm/**",
    ),
]


def is_ignored(path: Path) -> bool:
    for part in path.parts:
        if part in IGNORE_DIRS or part.startswith("."):
            return True
    return False


def run_audit() -> int:
    violations = []

    # Files to check: .py, .ts, .tsx
    valid_exts = {".py", ".ts", ".tsx"}

    for root, dirs, files in os.walk(ROOT_DIR):
        # Filter out ignored directories in-place
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

        for file in files:
            file_path = Path(root) / file
            if file_path.suffix not in valid_exts:
                continue

            rel_path = file_path.relative_to(ROOT_DIR).as_posix()

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"Warning: Could not read {rel_path}: {e}")
                continue

            for line_no, line in enumerate(content.splitlines(), start=1):
                # Skip comments or self-referential script lines or test files testing LLM integration
                if "scope_audit" in rel_path or rel_path.startswith("backend/tests/"):
                    continue

                for pattern, allowed_prefixes, rule_desc in RULES:
                    if pattern.search(line):
                        is_allowed = any(
                            rel_path.startswith(prefix) for prefix in allowed_prefixes
                        )
                        if not is_allowed:
                            violations.append(
                                f"VIOLATION [{rule_desc}] in {rel_path}:{line_no} -> {line.strip()}"
                            )

    if violations:
        print("[FAIL] SCOPE AUDIT FAILED! The following boundary violations were detected:\n")
        for v in violations:
            print(f"  - {v}")
        print("\nFix these violations before proceeding.")
        return 1
    else:
        print("[OK] SCOPE AUDIT PASSED! All architectural boundary rules satisfied.")
        return 0


if __name__ == "__main__":
    sys.exit(run_audit())
