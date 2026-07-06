"""Anthropic Claude provider (Messages API, streaming)."""

from __future__ import annotations

from .base import Provider, ProviderError


class ClaudeProvider(Provider):
    name = "claude"

    def generate(self, system: str, user: str) -> str:
        self.require_key("ANTHROPIC_API_KEY")
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "anthropic SDK not installed: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            # Stream so large max_tokens doesn't hit HTTP timeouts.
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise ProviderError(f"Claude rate limit; retry later: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Network error reaching Claude: {exc}") from exc

        if message.stop_reason == "refusal":
            raise ProviderError("Claude declined this topic; pick a different one.")
        return "".join(b.text for b in message.content if b.type == "text")
