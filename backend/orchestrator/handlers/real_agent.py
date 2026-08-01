"""Real Agent Handler executing LLM completions for declared AgentRoles."""

import logging
from typing import Any

from app.models import Artifact, Node, NodeStatus
from orchestrator.agents.parsing import parse_agent_output
from orchestrator.agents.roles import ROLES, AgentRole
from orchestrator.config import HandlerConfig
from orchestrator.handlers import ArtifactSpec, HandlerResult
from orchestrator.handlers.agent import handle_agent_node as handle_fake_agent_node
from orchestrator.llm import BaseLLMClient, LLMError

logger = logging.getLogger(__name__)


def truncate_middle_out(text: str, target_length: int) -> str:
    """Truncate text middle-out, inserting explicit truncation marker."""
    if len(text) <= target_length:
        return text

    overflow = len(text) - target_length
    marker = f"\n[... TRUNCATED {overflow} chars ...]\n"
    keep_each_side = (target_length - len(marker)) // 2

    if keep_each_side <= 0:
        return text[:target_length]

    return text[:keep_each_side] + marker + text[-keep_each_side:]


def assemble_user_message(
    inputs: dict[str, Artifact],
    role: AgentRole,
) -> str:
    """Assemble deterministic user message from resolved input artifacts matching declared selector order."""
    truncatable_sections: list[str] = []
    protected_sections: list[str] = []
    seen_art_ids: set[Any] = set()

    never_trunc_kinds = set(getattr(role, "never_truncate", []))

    for selector in role.input_selectors:
        matching_arts: list[Artifact] = []

        # 1. Direct lookup by key if present
        key = selector.get("kind") or selector.get("filename")
        if key and key in inputs and inputs[key].id not in seen_art_ids:
            art = inputs[key]
            matching_arts.append(art)
            seen_art_ids.add(art.id)

        # 2. Collect all candidates matching selector
        for candidate in inputs.values():
            if candidate.id in seen_art_ids:
                continue
            is_match = False
            if "kind" in selector and selector["kind"] and candidate.kind == selector["kind"]:
                is_match = True
            elif "filename" in selector and selector["filename"] and candidate.filename == selector["filename"]:
                is_match = True

            if is_match:
                matching_arts.append(candidate)
                seen_art_ids.add(candidate.id)

        for art in matching_arts:
            att_label = f", attempt {art.attempt}" if (getattr(art, "attempt", 1) or 1) > 1 else ""
            header = f"## INPUT: {art.kind} (from {art.produced_by_role or 'system'}, v{art.version}{att_label})"
            section = f"{header}\n{art.content}"

            if art.kind in never_trunc_kinds:
                protected_sections.append(section)
            else:
                truncatable_sections.append(section)

    all_sections = truncatable_sections + protected_sections
    assembled = "\n\n".join(all_sections)

    # Enforce Context Budget Max Chars with never_truncate protection
    if len(assembled) > role.max_input_chars:
        logger.warning(
            f"Assembled context length ({len(assembled)} chars) exceeds role max_input_chars ({role.max_input_chars} chars). Applying truncation."
        )
        if protected_sections:
            protected_text = "\n\n".join(protected_sections)
            protected_len = len(protected_text)
            allowed_trunc_len = max(role.max_input_chars - protected_len - 50, 1000)
            truncatable_text = truncate_middle_out("\n\n".join(truncatable_sections), allowed_trunc_len)
            assembled = f"{truncatable_text}\n\n{protected_text}"
        else:
            assembled = truncate_middle_out(assembled, role.max_input_chars)

    return assembled


async def handle_real_agent_node(
    node: Node,
    inputs: dict[str, Artifact],
    config: HandlerConfig,
    llm_client: BaseLLMClient | None = None,
) -> HandlerResult:
    """Execute LLM completion for an agent node using its declared AgentRole definition.

    Args:
        node: The Node database model instance.
        inputs: Resolved input artifacts mapping.
        config: HandlerConfig timing settings.
        llm_client: Injected BaseLLMClient instance.

    Returns:
        HandlerResult containing status, produced artifacts (including prompt/raw_response), and meta token metrics.
    """
    role = ROLES.get(node.agent_role or "")
    if role is None or llm_client is None:
        logger.info(
            f"No registered role or LLM client for agent node {node.name} (role={node.agent_role}). Falling back to fake agent handler."
        )
        return await handle_fake_agent_node(node, inputs, config)

    # 1. Assemble user message deterministically
    assembled_user_msg = assemble_user_message(inputs, role)

    # 2. Invoke LLM completion
    try:
        res = await llm_client.complete(
            system=role.system_prompt,
            messages=[{"role": "user", "content": assembled_user_msg}],
            max_tokens=role.max_tokens,
            temperature=role.temperature,
        )
    except LLMError as exc:
        logger.error(f"LLM call failed for node {node.name}: {exc}")
        return HandlerResult(
            status=NodeStatus.failed,
            logs=f"LLM error during execution: {exc}",
        )
    except Exception as exc:
        logger.exception(f"Unexpected exception during LLM execution for node {node.name}: {exc}")
        return HandlerResult(
            status=NodeStatus.failed,
            logs=f"Unexpected exception: {exc}",
        )

    meta = {
        "model": res.model,
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "latency_ms": res.latency_ms,
        "stop_reason": res.stop_reason,
    }

    # 3. Check for truncated output (stop_reason == 'max_tokens')
    if res.stop_reason == "max_tokens":
        logger.warning(f"Agent output truncated for node {node.name} (stop_reason=max_tokens)")
        return HandlerResult(
            status=NodeStatus.failed,
            artifacts=[ArtifactSpec(kind="raw_response", filename="raw_response.md", content=res.text)],
            logs="agent output truncated (stop_reason=max_tokens)",
            meta=meta,
        )

    # 4. Parse output files
    is_valid, parsed_specs, log_reason = parse_agent_output(res.text, role.outputs, role=role)

    if not is_valid:
        return HandlerResult(
            status=NodeStatus.failed,
            artifacts=parsed_specs,  # Contains raw_response artifact spec
            logs=log_reason,
            meta=meta,
        )

    # 5. Add prompt debugging artifact
    prompt_debug_spec = ArtifactSpec(
        kind="prompt",
        filename=f"prompt_{node.name}.md",
        content=f"=== SYSTEM PROMPT ===\n{role.system_prompt}\n\n=== USER PROMPT ===\n{assembled_user_msg}",
    )
    all_specs = parsed_specs + [prompt_debug_spec]

    return HandlerResult(
        status=NodeStatus.completed,
        artifacts=all_specs,
        logs=f"Real agent handler succeeded for node {node.name}",
        meta=meta,
    )
