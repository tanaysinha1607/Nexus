"""Fake Agent Node handler."""

import asyncio
import random
from typing import Any

from app.models import Artifact, Node, NodeStatus
from orchestrator.config import HandlerConfig
from orchestrator.handlers import ArtifactSpec, HandlerResult


async def handle_agent_node(
    node: Node,
    inputs: dict[str, Artifact],
    config: HandlerConfig,
) -> HandlerResult:
    """Execute a fake Agent node."""
    low, high = config.agent_sleep_range
    if high > low >= 0:
        await asyncio.sleep(random.uniform(low, high))

    role = node.agent_role or "agent"
    artifacts: list[ArtifactSpec] = []

    # Support multiple output_kinds (e.g. Architect emitting both api_contract and db_schema)
    output_kinds = node.config.get("output_kinds")
    if not output_kinds:
        output_kinds = [node.config.get("output_kind", "prd")]

    for kind in output_kinds:
        filename = f"{kind}.md"
        content = f"# Generated {kind.upper()}\nProduced by role: {role}\nNode: {node.name}"
        artifacts.append(
            ArtifactSpec(
                kind=kind,
                filename=filename,
                content=content,
                content_type="text/markdown",
            )
        )

    return HandlerResult(
        status=NodeStatus.completed,
        artifacts=artifacts,
        logs=f"Agent {role} finished successfully and produced {[a.filename for a in artifacts]}.",
    )
