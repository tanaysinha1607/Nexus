"""LLM Client Factory for Nexus Orchestration Engine."""

import os
from app.config import settings
from orchestrator.llm.groq_client import GroqLLMClient
from orchestrator.llm.llm_client import (
    AnthropicLLMClient,
    BaseLLMClient,
    FakeLLMClient,
    GeminiLLMClient,
)


def get_default_llm_client(use_real_agents: bool = True) -> BaseLLMClient:
    """Return configured LLM client instance based on NEXUS_LLM_PROVIDER or environment keys.

    Priority:
      1. FakeLLMClient if use_real_agents is False
      2. GroqLLMClient if NEXUS_LLM_PROVIDER=='groq' or GROQ_API_KEY is present
      3. GeminiLLMClient if NEXUS_LLM_PROVIDER=='gemini' or GEMINI_API_KEY is present
      4. AnthropicLLMClient if NEXUS_LLM_PROVIDER=='anthropic' or ANTHROPIC_API_KEY is present
      5. FakeLLMClient fallback for offline / test environments
    """
    if not use_real_agents:
        return FakeLLMClient()

    provider = (os.getenv("NEXUS_LLM_PROVIDER") or getattr(settings, "nexus_llm_provider", "")).lower()

    if provider == "groq":
        groq_key = os.getenv("GROQ_API_KEY") or settings.groq_api_key
        if groq_key and groq_key.strip():
            return GroqLLMClient(api_key=groq_key.strip())

    if provider == "gemini":
        gemini_key = os.getenv("GEMINI_API_KEY") or settings.gemini_api_key
        if gemini_key and gemini_key.strip():
            return GeminiLLMClient(api_key=gemini_key.strip())

    if provider == "anthropic":
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key and anthropic_key.strip():
            return AnthropicLLMClient(api_key=anthropic_key.strip())

    # Fallback to key-based inference if provider is not explicitly set or invalid
    groq_key = os.getenv("GROQ_API_KEY") or settings.groq_api_key
    if groq_key and groq_key.strip():
        return GroqLLMClient(api_key=groq_key.strip())

    gemini_key = os.getenv("GEMINI_API_KEY") or settings.gemini_api_key
    if gemini_key and gemini_key.strip():
        return GeminiLLMClient(api_key=gemini_key.strip())

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key and anthropic_key.strip():
        return AnthropicLLMClient(api_key=anthropic_key.strip())

    return FakeLLMClient()
