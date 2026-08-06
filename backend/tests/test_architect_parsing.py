"""Unit tests for Solution Architect dual artifact output parsing."""

import pytest
from orchestrator.agents.parsing import parse_agent_output
from orchestrator.agents.roles import SOLUTION_ARCHITECT_ROLE


def test_architect_dual_artifact_parsing():
    """Verify that an Architect response with architecture.md and build_manifest.json parses into 2 artifacts successfully."""
    raw_text = """=== FILE: architecture.md ===
```markdown
# Architecture Specification

## Overview
FastAPI task management API.
```

=== FILE: build_manifest.json ===
```json
{
  "language": "python",
  "framework": "fastapi",
  "entrypoint": "main.py",
  "test_command": "pytest",
  "build_command": "pip install -r requirements.txt"
}
```"""

    is_valid, artifact_specs, log_reason = parse_agent_output(
        raw_text, SOLUTION_ARCHITECT_ROLE.outputs, SOLUTION_ARCHITECT_ROLE
    )

    assert is_valid is True, f"Parsing failed with log_reason: {log_reason}"
    assert log_reason == "agent output successfully parsed"
    assert len(artifact_specs) == 2

    kinds = {spec.kind: spec for spec in artifact_specs}
    assert "architecture" in kinds
    assert "build_manifest" in kinds
    assert kinds["architecture"].filename == "architecture.md"
    assert kinds["build_manifest"].filename == "build_manifest.json"
    assert '"language": "python"' in kinds["build_manifest"].content
