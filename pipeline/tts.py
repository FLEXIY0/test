"""Voiceover via edge-tts (Microsoft neural voices, free, no API key).

Each script segment is synthesized to its own mp3 so segment durations are
exact — the image for a segment is shown precisely as long as its narration.
"""

from __future__ import annotations

import asyncio
import logging

import edge_tts

from .config import Settings
from .script_parser import ScriptDoc

logger = logging.getLogger("tts")


async def synthesize_segment(text: str, out_path: str, settings: Settings) -> None:
    communicate = edge_tts.Communicate(
        text=text, voice=settings.voice, rate=settings.voice_rate
    )
    await communicate.save(out_path)


async def synthesize_all(doc: ScriptDoc, workdir: str, settings: Settings) -> list[str]:
    """Synthesize every segment; returns the list of mp3 paths in order."""
    paths = [f"{workdir}/seg_{i:03d}.mp3" for i in range(len(doc.segments))]
    # Sequential on purpose: parallel sessions get throttled by the service.
    for i, segment in enumerate(doc.segments):
        await synthesize_segment(segment.text, paths[i], settings)
        logger.info("voiced segment %d/%d", i + 1, len(doc.segments))
    return paths


def run_tts(doc: ScriptDoc, workdir: str, settings: Settings) -> list[str]:
    return asyncio.run(synthesize_all(doc, workdir, settings))
