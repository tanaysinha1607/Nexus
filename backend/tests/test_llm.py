"""Unit and live integration tests for the orchestrator LLM client module."""

import os
import unittest.mock
import httpx
import pytest
from orchestrator.llm import AnthropicLLMClient, FakeLLMClient, LLMError, LLMResponse


@pytest.mark.asyncio
async def test_fake_llm_client_canned_responses():
    client = FakeLLMClient(
        canned_responses={"SUMMARY_MARKER": "Canned summary text"},
        default_response="Default fallback response",
    )

    # Call 1: Default response
    res1 = await client.complete(
        system="You are an assistant.",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert res1.text == "Default fallback response"
    assert res1.input_tokens > 0
    assert res1.output_tokens > 0
    assert res1.model == "fake-claude-sonnet-4-6"

    # Call 2: Marker matched response
    res2 = await client.complete(
        system="System prompt with SUMMARY_MARKER inside.",
        messages=[{"role": "user", "content": "Summarize"}],
    )
    assert res2.text == "Canned summary text"

    # Assert calls recorded for prompt inspection
    assert len(client.calls) == 2
    assert client.calls[0]["messages"][0]["content"] == "Hello"
    assert "SUMMARY_MARKER" in client.calls[1]["system"]


@pytest.mark.asyncio
async def test_anthropic_client_retries_on_429(monkeypatch):
    """Verifies that 429 Rate Limit errors trigger exponential backoff retries and succeed on attempt 3."""
    import anthropic

    client = AnthropicLLMClient(api_key="mock_key", timeout_seconds=10.0, max_retries=3)
    attempts = 0

    async def mock_create(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            mock_resp = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
            raise anthropic.APIStatusError(
                message="Rate limit exceeded", response=mock_resp, body={"error": {"type": "rate_limit_error"}}
            )

        resp = unittest.mock.MagicMock()
        resp.model = "claude-sonnet-4-6"
        resp.stop_reason = "end_turn"
        block = unittest.mock.MagicMock()
        block.text = "Success after retries"
        resp.content = [block]
        resp.usage.input_tokens = 12
        resp.usage.output_tokens = 8
        return resp

    async def dummy_sleep(duration):
        pass

    monkeypatch.setattr(client.client.messages, "create", mock_create)
    monkeypatch.setattr("asyncio.sleep", dummy_sleep)

    res = await client.complete("System prompt", [{"role": "user", "content": "Hi"}])
    assert attempts == 3
    assert res.text == "Success after retries"
    assert res.model == "claude-sonnet-4-6"
    assert res.input_tokens == 12
    assert res.output_tokens == 8


@pytest.mark.asyncio
async def test_anthropic_client_no_retry_on_400(monkeypatch):
    """Verifies that 400-class errors (e.g. BadRequestError) are NOT retried (exactly 1 attempt)."""
    import anthropic

    client = AnthropicLLMClient(api_key="mock_key", timeout_seconds=10.0, max_retries=3)
    attempts = 0

    async def mock_create_400(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        mock_resp = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        raise anthropic.BadRequestError(
            message="Invalid model parameter", response=mock_resp, body={"error": {"type": "invalid_request_error"}}
        )

    monkeypatch.setattr(client.client.messages, "create", mock_create_400)

    with pytest.raises(LLMError) as exc_info:
        await client.complete("System prompt", [{"role": "user", "content": "Hi"}])

    assert attempts == 1
    assert "Invalid model parameter" in str(exc_info.value)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_anthropic_completion():
    """Live API test executing against Anthropic API (skipped if ANTHROPIC_API_KEY is unset)."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY environment variable is not set")

    client = AnthropicLLMClient(api_key=api_key)
    res = await client.complete(
        system="Reply concisely in one sentence.",
        messages=[{"role": "user", "content": "Say hello from Nexus Phase 1.0"}],
        max_tokens=25,
    )

    assert isinstance(res, LLMResponse)
    assert len(res.text) > 0
    assert res.input_tokens > 0
    assert res.output_tokens > 0
    assert res.model == "claude-sonnet-4-6"
    print(
        f"\n[LIVE TEST OK] Model: {res.model}, Input Tokens: {res.input_tokens}, "
        f"Output Tokens: {res.output_tokens}, Stop Reason: {res.stop_reason}, Latency: {res.latency_ms}ms"
    )
