"""Provider factory tests (offline — no SDK calls, no network)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import load_settings
from pipeline.providers import ProviderError, get_provider
from pipeline.providers.claude_provider import ClaudeProvider
from pipeline.providers.gemini_provider import GeminiProvider
from pipeline.providers.qwen_provider import QwenProvider


def settings_with(**env):
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return load_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestFactory:
    def test_claude(self):
        p = get_provider(settings_with(SCRIPT_PROVIDER="claude", CLAUDE_MODEL="claude-opus-4-8"))
        assert isinstance(p, ClaudeProvider)
        assert p.model == "claude-opus-4-8"

    def test_gemini(self):
        p = get_provider(settings_with(SCRIPT_PROVIDER="gemini", GEMINI_MODEL="gemini-2.5-flash"))
        assert isinstance(p, GeminiProvider)
        assert p.model == "gemini-2.5-flash"

    def test_qwen(self):
        p = get_provider(settings_with(SCRIPT_PROVIDER="qwen", QWEN_MODEL="qwen-max"))
        assert isinstance(p, QwenProvider)
        assert p.model == "qwen-max"
        assert p.base_url.endswith("/v1")

    def test_unknown_raises(self):
        with pytest.raises(ProviderError):
            get_provider(settings_with(SCRIPT_PROVIDER="gpt5"))


class TestKeyGate:
    def test_missing_key_raises_clear_error(self):
        # No key configured -> generate() must fail fast before any SDK import.
        p = get_provider(settings_with(SCRIPT_PROVIDER="qwen", DASHSCOPE_API_KEY="", QWEN_API_KEY=""))
        with pytest.raises(ProviderError) as exc:
            p.generate("system", "user")
        assert "API key is not set" in str(exc.value)

    def test_gemini_missing_key(self):
        p = get_provider(settings_with(SCRIPT_PROVIDER="gemini", GEMINI_API_KEY="", GOOGLE_API_KEY=""))
        with pytest.raises(ProviderError):
            p.generate("system", "user")
