"""Voiceover via edge-tts (Microsoft neural voices, free, no API key).

Each script segment is synthesized to its own mp3 so segment durations are
exact. Word-boundary events from the TTS stream are captured per segment and
saved to JSON — they drive subtitle generation for free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import edge_tts

from .config import Settings
from .script_parser import ScriptDoc

logger = logging.getLogger("tts")


async def synthesize_segment(
    text: str, out_path: str, settings: Settings
) -> list[dict]:
    """Synthesize one segment; returns word cues [{start, end, text}] in
    seconds relative to the start of this segment's audio."""
    communicate = edge_tts.Communicate(
        text=text, voice=settings.voice, rate=settings.voice_rate
    )
    words: list[dict] = []
    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration are reported in 100-nanosecond ticks.
                start = chunk["offset"] / 1e7
                words.append({
                    "start": start,
                    "end": start + chunk["duration"] / 1e7,
                    "text": chunk["text"],
                })
    return words


async def synthesize_all(
    doc: ScriptDoc, workdir: str, settings: Settings
) -> tuple[list[str], list[list[dict]]]:
    paths = [os.path.join(workdir, f"seg_{i:03d}.mp3") for i in range(len(doc.segments))]
    all_words: list[list[dict]] = []
    # Sequential on purpose: parallel sessions get throttled by the service.
    for i, segment in enumerate(doc.segments):
        words = await synthesize_segment(segment.text, paths[i], settings)
        all_words.append(words)
        logger.info("voiced segment %d/%d (%d words)", i + 1, len(doc.segments), len(words))
    return paths, all_words


def run_tts(
    doc: ScriptDoc, workdir: str, settings: Settings
) -> tuple[list[str], list[list[dict]]]:
    """Synthesize all segments (or reuse existing files on a --resume run).

    Returns (audio paths, per-segment word cues). Cues are persisted to
    words.json in the workdir so resumed runs keep their subtitles.
    """
    words_path = os.path.join(workdir, "words.json")
    paths = [os.path.join(workdir, f"seg_{i:03d}.mp3") for i in range(len(doc.segments))]
    if all(os.path.exists(p) for p in paths) and os.path.exists(words_path):
        logger.info("reusing %d existing voiceover segments", len(paths))
        with open(words_path, "r", encoding="utf-8") as fh:
            return paths, json.load(fh)

    paths, all_words = asyncio.run(synthesize_all(doc, workdir, settings))
    with open(words_path, "w", encoding="utf-8") as fh:
        json.dump(all_words, fh)
    return paths, all_words
