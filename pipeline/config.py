"""Environment-driven configuration for the video pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _read_secret(name: str, default: str = "") -> str:
    """Read NAME_FILE (Docker secret) first, then the NAME env var."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- voiceover (edge-tts, free, no key needed) ---
    voice: str                 # e.g. en-US-ChristopherNeural (deep US male)
    voice_rate: str            # e.g. "-8%" — slightly slower suits older viewers

    # --- video ---
    width: int
    height: int
    fps: int
    music_file: str            # optional path to background music (user-provided)
    music_volume: float        # 0.0-1.0, ducked under narration

    # --- quality gates ---
    min_duration_s: float
    max_duration_s: float
    min_images: int

    # --- script generation (optional, needs ANTHROPIC_API_KEY) ---
    anthropic_api_key: str
    claude_model: str

    # --- paths ---
    output_dir: str
    assets_dir: str


def load_settings() -> Settings:
    return Settings(
        voice=os.environ.get("VOICE", "en-US-ChristopherNeural"),
        voice_rate=os.environ.get("VOICE_RATE", "-8%"),
        width=_env_int("VIDEO_WIDTH", 1920),
        height=_env_int("VIDEO_HEIGHT", 1080),
        fps=_env_int("VIDEO_FPS", 30),
        music_file=os.environ.get("MUSIC_FILE", ""),
        music_volume=_env_float("MUSIC_VOLUME", 0.10),
        min_duration_s=_env_float("MIN_DURATION_S", 240.0),
        max_duration_s=_env_float("MAX_DURATION_S", 900.0),
        min_images=_env_int("MIN_IMAGES", 5),
        anthropic_api_key=_read_secret("ANTHROPIC_API_KEY"),
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"),
        output_dir=os.environ.get("OUTPUT_DIR", "./output"),
        assets_dir=os.environ.get("ASSETS_DIR", "./assets"),
    )
