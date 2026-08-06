"""Unit tests for Senior Reviewer retry and graceful degradation behavior."""

import uuid
import pytest
from app.models import Artifact, Node, NodeStatus, NodeType
from orchestrator import HandlerConfig
from orchestrator.handlers.real_agent import handle_real_agent_node
from orchestrator.llm.llm_client import BaseLLMClient, LLMResponse


class FlakyLLMClient(BaseLLMClient):
    """Fake LLM client that returns configured sequence of responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    async def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        idx = min(self.call_count, len(self.responses) - 1)
        resp_text = self.responses[idx]
        self.call_count += 1
        return LLMResponse(
            text=resp_text,
            input_tokens=100,
            output_tokens=100,
            model="fake-flaky-model",
            stop_reason="end_turn",
            latency_ms=10,
        )


@pytest.mark.asyncio
async def test_reviewer_retry_succeeds_on_second_attempt():
    """Verify that a transient empty response on attempt 1 retries and succeeds on attempt 2."""
    flaky_llm = FlakyLLMClient(
        responses=[
            "",  # transient empty response
            "=== FILE: review.md ===\n```markdown\n# Code Review\n\nREVIEW_VERDICT: approved\n```",
        ]
    )

    node = Node(
        id=uuid.uuid4(),
        name="Reviewer",
        node_type=NodeType.agent,
        agent_role="senior_reviewer",
    )
    verdict_art = Artifact(kind="verdict", filename="verdict.json", content='{"passed": true}')
    api_art = Artifact(kind="api_contract", filename="api_contract.json", content='{"endpoints": []}')
    inputs = {"verdict": verdict_art, "api_contract": api_art}

    res = await handle_real_agent_node(node, inputs, HandlerConfig(), llm_client=flaky_llm)

    assert res.status == NodeStatus.completed
    assert flaky_llm.call_count == 2
    kinds = {a.kind: a for a in res.artifacts}
    assert "review" in kinds
    assert "REVIEW_VERDICT: approved" in kinds["review"].content


@pytest.mark.asyncio
async def test_reviewer_degrades_gracefully_when_all_retries_fail():
    """Verify that if all retries remain empty/unparseable, Reviewer node degrades gracefully to completed fallback approval."""
    always_empty_llm = FlakyLLMClient(responses=["", "", ""])

    node = Node(
        id=uuid.uuid4(),
        name="Reviewer",
        node_type=NodeType.agent,
        agent_role="senior_reviewer",
    )
    verdict_art = Artifact(kind="verdict", filename="verdict.json", content='{"passed": true}')
    api_art = Artifact(kind="api_contract", filename="api_contract.json", content='{"endpoints": []}')
    inputs = {"verdict": verdict_art, "api_contract": api_art}

    res = await handle_real_agent_node(node, inputs, HandlerConfig(), llm_client=always_empty_llm)

    assert res.status == NodeStatus.completed
    assert always_empty_llm.call_count == 3
    kinds = {a.kind: a for a in res.artifacts}
    assert "review" in kinds
    assert "REVIEW_VERDICT: approved" in kinds["review"].content
    assert "Transient Fallback" in kinds["review"].content


@pytest.mark.asyncio
async def test_genuine_changes_requested_still_triggers_rejection():
    """Verify that a genuine REVIEW_VERDICT: changes_requested output produces review artifact properly."""
    rejection_llm = FlakyLLMClient(
        responses=[
            "=== FILE: review.md ===\n```markdown\n# Code Review\n\nREVIEW_VERDICT: changes_requested\n```"
        ]
    )

    node = Node(
        id=uuid.uuid4(),
        name="Reviewer",
        node_type=NodeType.agent,
        agent_role="senior_reviewer",
    )
    verdict_art = Artifact(kind="verdict", filename="verdict.json", content='{"passed": true}')
    api_art = Artifact(kind="api_contract", filename="api_contract.json", content='{"endpoints": []}')
    inputs = {"verdict": verdict_art, "api_contract": api_art}

    res = await handle_real_agent_node(node, inputs, HandlerConfig(), llm_client=rejection_llm)

    assert res.status == NodeStatus.completed
    assert rejection_llm.call_count == 1
    kinds = {a.kind: a for a in res.artifacts}
    assert "review" in kinds
    assert "REVIEW_VERDICT: changes_requested" in kinds["review"].content
