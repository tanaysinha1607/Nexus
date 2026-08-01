"""Groq LLM Client implementation using OpenAI-compatible AsyncOpenAI SDK."""

import asyncio
import logging
import os
import random
import re
import time
from typing import Any

import openai

from app.config import settings
from orchestrator.llm.llm_client import BaseLLMClient, LLMError, LLMResponse

logger = logging.getLogger(__name__)


class GroqLLMClient(BaseLLMClient):
    """Production Groq LLM client using OpenAI-compatible AsyncOpenAI SDK with retries."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 300.0,
        max_retries: int = 3,
        base_url: str = "https://api.groq.com/openai/v1",
        tpm_budget: int = 7500,
    ) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY") or settings.groq_api_key
        if not self.api_key:
            raise LLMError("GROQ_API_KEY environment variable is not set")

        self.model = model or os.getenv("NEXUS_LLM_MODEL") or settings.nexus_llm_model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.base_url = base_url
        self.tpm_budget = tpm_budget
        self.client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        attempt = 0
        start_time = time.monotonic()

        # Build payload messages list with system prompt
        payload_messages = []
        total_input_chars = len(system or "")
        if system:
            payload_messages.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            total_input_chars += len(content)
            payload_messages.append({"role": role, "content": content})

        # PRE-FLIGHT TPM GUARDRAIL: Groq 8000 TPM = input + max_tokens
        estimated_input_tokens = total_input_chars // 4
        requested_tokens = estimated_input_tokens + max_tokens
        if requested_tokens > self.tpm_budget:
            raise LLMError(
                f"request {requested_tokens} tokens (input ~{estimated_input_tokens} + max_tokens {max_tokens}) "
                f"exceeds TPM budget {self.tpm_budget} — reduce max_tokens or input size"
            )

        # Send ONLY required parameters to avoid Groq 400 errors on optional OpenAI fields
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        while True:
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**request_kwargs),
                    timeout=self.timeout_seconds,
                )

                latency_ms = int((time.monotonic() - start_time) * 1000)

                # Extract response text and usage
                choice = response.choices[0] if response.choices else None
                text_content = choice.message.content if choice and choice.message else ""

                usage = response.usage
                input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(usage, "completion_tokens", 0) or 0

                # Map finish_reason (CRITICAL: 'length' -> 'max_tokens')
                finish = getattr(choice, "finish_reason", "stop") or "stop"
                finish_str = str(finish).lower()

                if finish_str in ("length", "max_tokens"):
                    stop_reason = "max_tokens"
                elif finish_str in ("stop", "end_turn"):
                    stop_reason = "end_turn"
                else:
                    stop_reason = finish_str

                logger.info(
                    f"GROQ COMPLETION SUCCESS: model={self.model}, "
                    f"input_tokens={input_tokens}, "
                    f"output_tokens={output_tokens}, "
                    f"latency_ms={latency_ms}ms"
                )

                return LLMResponse(
                    text=text_content or "",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=getattr(response, "model", self.model),
                    stop_reason=stop_reason,
                    latency_ms=latency_ms,
                )

            except Exception as exc:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                exc_str = str(exc).lower()

                # Detect per-minute TPM rate limits (413 or 429 with TPM / rate_limit_exceeded keywords)
                is_tpm_limit = status_code in (413, 429) or ("tpm" in exc_str or "rate_limit_exceeded" in exc_str or "tokens per minute" in exc_str)
                is_server_error = status_code is not None and isinstance(status_code, int) and status_code >= 500
                is_timeout = isinstance(exc, asyncio.TimeoutError)

                should_retry = (is_tpm_limit or is_server_error or is_timeout) and attempt < self.max_retries

                if not should_retry:
                    logger.error(
                        f"GROQ COMPLETION PERMANENT FAILURE (attempt {attempt}/{self.max_retries}): {exc}"
                    )
                    raise LLMError(f"Groq API call failed: {exc}", cause=exc) from exc

                # Extract retry hint from error text if available (e.g. "Please retry in 16.1s")
                retry_match = re.search(r"retry\s+in\s+([\d\.]+)\s*s", exc_str)
                if retry_match:
                    try:
                        backoff_seconds = float(retry_match.group(1)) + 1.0
                    except ValueError:
                        backoff_seconds = (2 ** (attempt - 1)) * 5.0 + random.uniform(0.1, 0.5)
                else:
                    backoff_seconds = (2 ** (attempt - 1)) * 5.0 + random.uniform(0.1, 0.5)

                logger.warning(
                    f"GROQ COMPLETION RETRYABLE RATE LIMIT/ERROR (attempt {attempt}/{self.max_retries}): {exc}. "
                    f"Retrying in {backoff_seconds:.2f}s..."
                )
                await asyncio.sleep(backoff_seconds)
