"""LLM Client Abstraction for Nexus Orchestration Engine.

DESIGN CONTRACT:
  - BaseLLMClient defines the unified async completion interface.
  - GeminiLLMClient wraps the Google Generative AI SDK (google-genai) with retries and error mapping.
  - AnthropicLLMClient wraps the Anthropic SDK with retries, timeouts, error mapping, and logging.
  - FakeLLMClient provides zero-network canned responses for unit tests.
  - NOTHING outside orchestrator/llm/ may import the google-genai or anthropic SDK.
"""

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Custom exception raised when an LLM completion request fails permanently."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass
class LLMResponse:
    """Standardized response from an LLM completion request."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str
    latency_ms: int


class BaseLLMClient(ABC):
    """Abstract base class for all LLM client implementations."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a completion request to the LLM backend."""
        pass


class GeminiLLMClient(BaseLLMClient):
    """Production Google Gemini client using the google-genai SDK with retries."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        import os
        from google import genai

        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or settings.gemini_api_key
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY environment variable is not set")

        self.model = model or os.getenv("NEXUS_LLM_MODEL") or settings.nexus_llm_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.client = genai.Client(api_key=self.api_key)

    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        from google.genai import types

        attempt = 0
        start_time = time.monotonic()

        # Convert messages to Gemini content format
        contents = []
        for msg in messages:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=msg["content"])],
                )
            )

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        while True:
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=self.timeout_seconds,
                )

                latency_ms = int((time.monotonic() - start_time) * 1000)

                # Extract text from response
                text_content = response.text or ""

                # Extract token counts from usage metadata
                usage = response.usage_metadata
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0

                # Map finish reason
                stop_reason = "end_turn"
                if response.candidates and response.candidates[0].finish_reason:
                    finish = str(response.candidates[0].finish_reason).lower()
                    if "max_tokens" in finish or "length" in finish:
                        stop_reason = "max_tokens"
                    elif "stop" in finish:
                        stop_reason = "end_turn"
                    elif "safety" in finish:
                        stop_reason = "safety"
                    else:
                        stop_reason = finish

                logger.info(
                    f"LLM COMPLETION SUCCESS: model={self.model}, "
                    f"input_tokens={input_tokens}, "
                    f"output_tokens={output_tokens}, "
                    f"latency_ms={latency_ms}ms"
                )

                return LLMResponse(
                    text=text_content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=self.model,
                    stop_reason=stop_reason,
                    latency_ms=latency_ms,
                )

            except Exception as exc:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)

                # Retryable: 429, 5xx, timeout, connection errors
                is_rate_limit = status_code == 429
                is_server_error = status_code is not None and isinstance(status_code, int) and status_code >= 500
                is_timeout = isinstance(exc, asyncio.TimeoutError)

                should_retry = (is_rate_limit or is_server_error or is_timeout) and attempt < self.max_retries

                if not should_retry:
                    logger.error(
                        f"LLM COMPLETION PERMANENT FAILURE (attempt {attempt}/{self.max_retries}): {exc}"
                    )
                    raise LLMError(f"Gemini API call failed: {exc}", cause=exc) from exc

                backoff_seconds = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                logger.warning(
                    f"LLM COMPLETION RETRYABLE ERROR (attempt {attempt}/{self.max_retries}): {exc}. "
                    f"Retrying in {backoff_seconds:.2f}s..."
                )
                await asyncio.sleep(backoff_seconds)


class AnthropicLLMClient(BaseLLMClient):
    """Production Anthropic Claude client with exponential backoff retries and logging."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        import os
        import anthropic

        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY environment variable is not set")

        # Single Source of Truth for LLM model from settings.nexus_llm_model
        self.model = model or os.getenv("NEXUS_LLM_MODEL") or settings.nexus_llm_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        import anthropic

        attempt = 0
        start_time = time.monotonic()

        while True:
            attempt += 1
            try:
                # Wrap with per-call timeout
                response = await asyncio.wait_for(
                    self.client.messages.create(
                        model=self.model,
                        system=system,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ),
                    timeout=self.timeout_seconds,
                )

                latency_ms = int((time.monotonic() - start_time) * 1000)
                text_content = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text_content += block.text

                logger.info(
                    f"LLM COMPLETION SUCCESS: model={response.model}, "
                    f"input_tokens={response.usage.input_tokens}, "
                    f"output_tokens={response.usage.output_tokens}, "
                    f"latency_ms={latency_ms}ms"
                )

                return LLMResponse(
                    text=text_content,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=response.model,
                    stop_reason=response.stop_reason or "stop",
                    latency_ms=latency_ms,
                )

            except Exception as exc:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                status_code = getattr(exc, "status_code", None)

                # Check if error is retryable (429 Rate Limit, 5xx Server Error, or Connection Error / Timeout)
                is_rate_limit = status_code == 429
                is_server_error = status_code is not None and status_code >= 500
                is_conn_error = isinstance(exc, (anthropic.APIConnectionError, asyncio.TimeoutError))

                should_retry = (is_rate_limit or is_server_error or is_conn_error) and attempt < self.max_retries

                if not should_retry:
                    logger.error(
                        f"LLM COMPLETION PERMANENT FAILURE (attempt {attempt}/{self.max_retries}): {exc}"
                    )
                    raise LLMError(f"Anthropic API call failed: {exc}", cause=exc) from exc

                # Exponential backoff with jitter
                backoff_seconds = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                logger.warning(
                    f"LLM COMPLETION RETRYABLE ERROR (attempt {attempt}/{self.max_retries}): {exc}. "
                    f"Retrying in {backoff_seconds:.2f}s..."
                )
                await asyncio.sleep(backoff_seconds)


class FakeLLMClient(BaseLLMClient):
    """Deterministic, zero-network LLM client for unit tests."""

    def __init__(
        self,
        canned_responses: dict[str, str] | None = None,
        default_response: str = "Fake LLM completion output",
    ) -> None:
        self.canned_responses = {
            "product_manager": (
                "=== FILE: prd.md ===\n"
                "```markdown\n"
                "# Product Requirement Document (PRD)\n\n"
                "## Executive Summary & Problem Statement\n"
                "A high-performance cryptocurrency paper trading platform enabling real-time market simulation, "
                "portfolio analytics, and secure user management without real financial risk.\n\n"
                "## Key Features & Breakdown\n"
                "1. Authentication & Role-Based Access Control (JWT)\n"
                "2. Live Interactive Price Charts & Order Book\n"
                "3. Portfolio Management & Trade Execution Engine (Market & Limit orders)\n"
                "4. Administrative Dashboard & Audit Logging\n\n"
                "## Technical Milestones\n"
                "- Phase 1: Authentication & Core Schema\n"
                "- Phase 2: Trading Engine & Analytics Dashboard\n"
                "```"
            ),
            "solution_architect": (
                "=== FILE: architecture.md ===\n"
                "```markdown\n"
                "# Architecture Specification\n\n"
                "## 1. System Overview\n"
                "FastAPI + PostgreSQL + Redis architecture.\n"
                "```\n\n"
                "=== FILE: build_manifest.json ===\n"
                "```json\n"
                "{\n"
                '  "language": "python",\n'
                '  "framework": "fastapi",\n'
                '  "entrypoint": "main.py",\n'
                '  "test_command": "pytest",\n'
                '  "build_command": "pip install -r requirements.txt"\n'
                "}\n"
                "```"
            ),
            "api_designer": (
                "=== FILE: api_contract.json ===\n"
                "```json\n"
                "{\n"
                '  "endpoints": [\n'
                "    {\n"
                '      "method": "POST",\n'
                '      "path": "/api/v1/auth/register",\n'
                '      "summary": "User registration",\n'
                '      "request_schema": {"type": "object"},\n'
                '      "response_schema": {"type": "object"},\n'
                '      "auth_required": false\n'
                "    }\n"
                "  ]\n"
                "}\n"
                "```"
            ),
            "backend_engineer": (
                "=== FILE: main.py ===\n"
                "```python\n"
                "from fastapi import FastAPI\n\n"
                "app = FastAPI()\n\n"
                '@app.get("/health")\n'
                "def health():\n"
                '    return {"status": "ok"}\n'
                "```\n\n"
                "=== FILE: requirements.txt ===\n"
                "```text\n"
                "fastapi==0.115.0\n"
                "uvicorn==0.30.0\n"
                "```"
            ),
        }
        if canned_responses:
            self.canned_responses.update(canned_responses)
        self.default_response = default_response
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        self.calls.append({
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })

        text_out = self.default_response
        # Prioritize matching system prompt markers (e.g. role name) over user messages
        for marker, canned_text in self.canned_responses.items():
            if marker in system:
                text_out = canned_text
                break
        else:
            for marker, canned_text in self.canned_responses.items():
                if any(marker in str(m.get("content", "")) for m in messages):
                    text_out = canned_text
                    break

        return LLMResponse(
            text=text_out,
            input_tokens=len(system) // 4 + 10,
            output_tokens=len(text_out) // 4 + 5,
            model="fake-claude-sonnet-4-6",
            stop_reason="end_turn",
            latency_ms=1,
        )
