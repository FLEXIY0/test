"""SRT subtitles from edge-tts word boundaries.

Word cues arrive per segment with timestamps relative to that segment's audio.
Since segments are concatenated back-to-back, absolute timestamps are the
segment-relative ones shifted by the sum of preceding segment durations.
"""

from __future__ import annotations

MAX_CUE_CHARS = 42
MAX_CUE_SECONDS = 5.0
MAX_WORD_GAP = 0.8


def group_words(words: list[dict]) -> list[dict]:
    """Group word cues into readable subtitle cues [{start, end, text}]."""
    cues: list[dict] = []
    current: dict | None = None
    for word in words:
        text = word["text"].strip()
        if not text:
            continue
        if current is not None:
            too_long = len(current["text"]) + 1 + len(text) > MAX_CUE_CHARS
            too_slow = word["end"] - current["start"] > MAX_CUE_SECONDS
            big_gap = word["start"] - current["end"] > MAX_WORD_GAP
            if too_long or too_slow or big_gap:
                cues.append(current)
                current = None
        if current is None:
            current = {"start": word["start"], "end": word["end"], "text": text}
        else:
            current["text"] += f" {text}"
            current["end"] = word["end"]
    if current is not None:
        cues.append(current)
    return cues


def _timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(
    segment_words: list[list[dict]],
    segment_durations: list[float],
    out_path: str,
) -> int:
    """Write an .srt file; returns the number of cues written."""
    index = 1
    offset = 0.0
    lines: list[str] = []
    for words, duration in zip(segment_words, segment_durations):
        for cue in group_words(words):
            start = min(cue["start"], duration) + offset
            end = min(cue["end"], duration) + offset
            lines += [str(index), f"{_timestamp(start)} --> {_timestamp(end)}",
                      cue["text"], ""]
            index += 1
        offset += duration
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return index - 1
