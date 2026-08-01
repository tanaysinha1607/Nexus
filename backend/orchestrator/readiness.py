"""Readiness resolution logic for Nexus DAG nodes.

ATTEMPT SCOPING RATIONALE:
  Once the rework loop exists, an attempt-1 node that hasn't been claimed yet must
  NOT resolve to attempt-2 artifacts. Attempt scoping prevents cross-attempt bleed.
  A selector matches an artifact only if artifact.attempt <= node.attempt.
  Among matches, prefer the HIGHEST attempt <= node.attempt, then highest version,
  then latest created_at.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Node


def matches_selector(artifact: Artifact, selector: dict[str, Any], node_attempt: int = 1) -> bool:
    """Check if an artifact satisfies a selector dictionary and node attempt bounds."""
    if not selector:
        return False

    # Attempt scoping rule: artifact attempt must not exceed node attempt
    art_attempt = getattr(artifact, "attempt", 1)
    if art_attempt > node_attempt:
        return False

    if selector.get("exact_attempt"):
        if art_attempt != node_attempt:
            return False

    if "kind" in selector and selector["kind"] is not None:
        if artifact.kind != selector["kind"]:
            return False

    if "from_role" in selector and selector["from_role"] is not None:
        if artifact.produced_by_role != selector["from_role"]:
            return False

    if "filename" in selector and selector["filename"] is not None:
        if artifact.filename != selector["filename"]:
            return False

    return True


async def resolve_node_readiness(
    session: AsyncSession,
    node: Node,
    run_id: uuid.UUID,
) -> tuple[bool, dict[str, Artifact]]:
    """Determine if a node is ready by checking if all required input selectors match.

    Returns:
        tuple[bool, dict[str, Artifact]]:
            - is_ready (bool)
            - resolved_inputs (dict mapping selector key/kind to the selected Artifact)
    """
    required_inputs = node.config.get("required_inputs", [])
    if not required_inputs:
        return True, {}

    # Fetch all artifacts produced in the same run
    # Ordered by: attempt DESC, version DESC, created_at DESC
    stmt = (
        select(Artifact)
        .where(Artifact.run_id == run_id)
        .order_by(Artifact.attempt.desc(), Artifact.version.desc(), Artifact.created_at.desc())
    )
    result = await session.execute(stmt)
    artifacts = list(result.scalars().all())

    resolved_inputs: dict[str, Artifact] = {}
    node_attempt = getattr(node, "attempt", 1)

    for idx, selector in enumerate(required_inputs):
        if not isinstance(selector, dict) or not any(
            k in selector for k in ("kind", "from_role", "filename")
        ):
            # Invalid selector format
            return False, {}

        # Find matching artifacts respecting attempt <= node_attempt
        matching = [art for art in artifacts if matches_selector(art, selector, node_attempt)]
        if not matching:
            if selector.get("optional"):
                continue
            return False, {}

        # Best attempt is matching[0].attempt due to ORDER BY attempt DESC
        best_attempt = matching[0].attempt
        attempt_matches = [art for art in matching if art.attempt == best_attempt]

        # 1. Primary selector key (satisfies unit tests expecting resolved[selector.kind])
        key = selector.get("kind") or selector.get("filename") or f"input_{idx}"
        resolved_inputs[key] = matching[0]

        # 2. Add each matching artifact by filename so multi-file outputs (main.py, requirements.txt) are all preserved for executor
        for art in attempt_matches:
            if art.filename:
                resolved_inputs[art.filename] = art

    # Special condition for Backend Engineer rework: requires at least failure_context, review_feedback, or test_failure for attempt=node_attempt
    if node.agent_role == "backend_engineer" and node_attempt > 1:
        has_rework_feedback = any(
            k in resolved_inputs for k in ("failure_context", "review_feedback", "test_failure")
        )
        if not has_rework_feedback:
            return False, {}

    # Special condition for Senior Reviewer: requires a PASSING validator verdict
    if node.agent_role == "senior_reviewer":
        verdict_art = resolved_inputs.get("verdict")
        if verdict_art:
            try:
                import json
                verdict_data = json.loads(verdict_art.content)
                if not verdict_data.get("passed", False):
                    return False, {}
            except Exception:
                return False, {}

    return True, resolved_inputs
