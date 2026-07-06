"""Google Gemini provider (google-genai SDK)."""

from __future__ import annotations

from .base import Provider, ProviderError


class GeminiProvider(Provider):
    name = "gemini"

    def generate(self, system: str, user: str) -> str:
        self.require_key("GEMINI_API_KEY (or GOOGLE_API_KEY)")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderError(
                "google-genai not installed: pip install google-genai"
            ) from exc

        client = genai.Client(api_key=self.api_key)
        try:
            resp = client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=self.max_tokens,
                ),
            )
        except Exception as exc:  # SDK raises various google.genai.errors.*
            raise ProviderError(f"Gemini API error: {exc}") from exc

        text = getattr(resp, "text", None)
        if not text:
            # Empty text usually means a safety block or an empty candidate.
            reason = getattr(resp, "prompt_feedback", None)
            raise ProviderError(
                f"Gemini returned no text (possibly safety-blocked): {reason}"
            )
        return text
