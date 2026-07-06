"""LLM provider registry: pick claude / gemini / qwen via SCRIPT_PROVIDER."""

from __future__ import annotations

from ..config import Settings
from .base import Provider, ProviderError
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .qwen_provider import QwenProvider

SUPPORTED = ("claude", "gemini", "qwen")


def get_provider(settings: Settings) -> Provider:
    """Construct the provider selected by settings.script_provider."""
    name = settings.script_provider
    if name == "claude":
        return ClaudeProvider(
            settings.claude_model, settings.anthropic_api_key, settings.max_output_tokens
        )
    if name == "gemini":
        return GeminiProvider(
            settings.gemini_model, settings.gemini_api_key, settings.max_output_tokens
        )
    if name == "qwen":
        return QwenProvider(
            settings.qwen_model, settings.qwen_api_key,
            settings.max_output_tokens, settings.qwen_base_url,
        )
    raise ProviderError(
        f"unknown SCRIPT_PROVIDER '{name}' — use one of: {', '.join(SUPPORTED)}"
    )


__all__ = ["Provider", "ProviderError", "get_provider", "SUPPORTED"]
