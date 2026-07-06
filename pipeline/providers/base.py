"""Common provider interface for LLM text generation.

Each provider wraps one vendor's official SDK behind a single method:
    generate(system, user) -> str

SDK imports are lazy (inside generate) so the core pipeline runs free without
any LLM package installed — you only install the SDK for the provider you pick.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Raised for any provider failure (missing key, SDK, API error, refusal)."""


class Provider(ABC):
    name = "base"

    def __init__(self, model: str, api_key: str, max_tokens: int) -> None:
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens

    def require_key(self, env_hint: str) -> None:
        if not self.api_key:
            raise ProviderError(
                f"{self.name}: API key is not set. Provide it via {env_hint} "
                f"(env var or Docker secret)."
            )

    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        """Return the raw model text for the given system + user prompt."""
