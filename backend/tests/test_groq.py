"""Unit and integration tests for Groq LLM Provider."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config import settings
from orchestrator.llm.factory import get_default_llm_client
from orchestrator.llm.groq_client import GroqLLMClient
from orchestrator.llm.llm_client import FakeLLMClient


def test_factory_returns_groq_llm_client(monkeypatch):
    monkeypatch.setenv("NEXUS_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_123")

    client = get_default_llm_client(use_real_agents=True)
    assert isinstance(client, GroqLLMClient)
    assert client.api_key == "gsk_test_mock_key_123"


def test_factory_returns_fake_client_when_use_real_agents_false(monkeypatch):
    monkeypatch.setenv("NEXUS_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_123")

    client = get_default_llm_client(use_real_agents=False)
    assert isinstance(client, FakeLLMClient)


@pytest.mark.asyncio
async def test_groq_finish_reason_length_maps_to_max_tokens(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_123")

    groq_client = GroqLLMClient(api_key="gsk_test_mock_key_123", model="openai/gpt-oss-120b")

    # Mock response object from AsyncOpenAI
    mock_choice = MagicMock()
    mock_choice.message.content = "Truncated text completion..."
    mock_choice.finish_reason = "length"

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 150
    mock_usage.completion_tokens = 4096

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    mock_response.model = "openai/gpt-oss-120b"

    mock_create = AsyncMock(return_value=mock_response)

    with patch.object(groq_client.client.chat.completions, "create", mock_create):
        res = await groq_client.complete(
            system="You are an AI architect.",
            messages=[{"role": "user", "content": "Generate architecture"}],
            max_tokens=3000,
            temperature=0.2,
        )

        assert res.stop_reason == "max_tokens"
        assert res.input_tokens == 150
        assert res.output_tokens == 4096
        assert res.text == "Truncated text completion..."


@pytest.mark.asyncio
async def test_groq_preflight_tpm_guardrail_fails_fast(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_123")

    groq_client = GroqLLMClient(
        api_key="gsk_test_mock_key_123",
        model="openai/gpt-oss-120b",
        tpm_budget=7500,
    )

    # Large input system prompt (~20,000 chars -> ~5,000 estimated input tokens)
    large_system = "A" * 20_000
    mock_create = AsyncMock()

    with patch.object(groq_client.client.chat.completions, "create", mock_create):
        from orchestrator.llm.llm_client import LLMError

        with pytest.raises(LLMError) as exc_info:
            await groq_client.complete(
                system=large_system,
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=3000,  # 5000 + 3000 = 8000 > 7500 budget
            )

        assert "exceeds TPM budget 7500" in str(exc_info.value)
        # Verify network API call was NEVER executed
        assert not mock_create.called


@pytest.mark.asyncio
async def test_groq_request_payload_kwargs_has_no_n_field(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_mock_key_123")

    groq_client = GroqLLMClient(api_key="gsk_test_mock_key_123", model="openai/gpt-oss-120b")

    mock_choice = MagicMock()
    mock_choice.message.content = "Successful completion"
    mock_choice.finish_reason = "stop"

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 50
    mock_usage.completion_tokens = 100

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    mock_response.model = "openai/gpt-oss-120b"

    mock_create = AsyncMock(return_value=mock_response)

    with patch.object(groq_client.client.chat.completions, "create", mock_create):
        await groq_client.complete(
            system="System prompt",
            messages=[{"role": "user", "content": "User prompt"}],
            max_tokens=2048,
            temperature=0.1,
        )

        assert mock_create.called
        call_kwargs = mock_create.call_args.kwargs

        # Verify minimal request kwargs: model, messages, max_tokens, temperature
        assert call_kwargs["model"] == "openai/gpt-oss-120b"
        assert call_kwargs["max_tokens"] == 2048
        assert call_kwargs["temperature"] == 0.1

        # Assert explicitly NO 'n' field or extra OpenAI optional fields
        assert "n" not in call_kwargs
        assert "top_p" not in call_kwargs
        assert "stream" not in call_kwargs


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set in environment",
)
@pytest.mark.asyncio
async def test_live_groq_trivial_roundtrip():
    groq_key = os.getenv("GROQ_API_KEY")
    client = GroqLLMClient(api_key=groq_key)
    res = await client.complete(
        system="Reply with a single word.",
        messages=[{"role": "user", "content": "Say hello"}],
        max_tokens=10,
        temperature=0.0,
    )
    assert res.text and len(res.text.strip()) > 0
    assert res.stop_reason == "end_turn"
