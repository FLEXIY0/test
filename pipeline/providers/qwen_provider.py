"""Alibaba Qwen provider via the DashScope OpenAI-compatible endpoint.

Qwen is served through an OpenAI-compatible API, so the official `openai` SDK
is the vendor-recommended client — point it at the DashScope base URL and pass
the DashScope API key.
"""

from __future__ import annotations

from .base import Provider, ProviderError


class QwenProvider(Provider):
    name = "qwen"

    def __init__(self, model: str, api_key: str, max_tokens: int, base_url: str) -> None:
        super().__init__(model, api_key, max_tokens)
        self.base_url = base_url

    def generate(self, system: str, user: str) -> str:
        self.require_key("DASHSCOPE_API_KEY (or QWEN_API_KEY)")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError(
                "openai SDK not installed (used for Qwen's OpenAI-compatible "
                "endpoint): pip install openai"
            ) from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # openai.APIError and subclasses
            raise ProviderError(f"Qwen API error: {exc}") from exc

        choices = getattr(resp, "choices", None)
        if not choices or not choices[0].message.content:
            raise ProviderError("Qwen returned empty content")
        return choices[0].message.content
