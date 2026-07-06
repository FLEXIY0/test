"""Quality gates run on the finished video before it is considered uploadable."""

from __future__ import annotations

import json
import os
import subprocess

from .config import Settings
from .script_parser import ScriptDoc


def probe_streams(path: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-300:]}")
    return json.loads(proc.stdout)


def run_checks(
    video_path: str, doc: ScriptDoc, image_count: int, settings: Settings
) -> dict:
    """Returns {"passed": bool, "checks": [...]} — every gate with its result."""
    checks: list[dict] = []

    def gate(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    exists = os.path.exists(video_path)
    gate("file_exists", exists, video_path)
    if exists:
        size_mb = os.path.getsize(video_path) / 1e6
        gate("file_size", size_mb > 5, f"{size_mb:.1f} MB")
        info = probe_streams(video_path)
        codecs = {s.get("codec_type") for s in info.get("streams", [])}
        gate("has_video_stream", "video" in codecs, str(codecs))
        gate("has_audio_stream", "audio" in codecs, str(codecs))
        duration = float(info.get("format", {}).get("duration", 0.0))
        gate(
            "duration_in_range",
            settings.min_duration_s <= duration <= settings.max_duration_s,
            f"{duration:.0f}s (target {settings.min_duration_s:.0f}-{settings.max_duration_s:.0f}s)",
        )

    gate("min_images", image_count >= settings.min_images, f"{image_count} images")
    gate("title_length", 10 <= len(doc.title) <= 100, f"{len(doc.title)} chars")
    gate("description_present", len(doc.description) >= 30, f"{len(doc.description)} chars")
    gate("tags_present", len(doc.tags) >= 5, f"{len(doc.tags)} tags")
    # YouTube's 2026 inauthentic-content policy: synthetic media must be
    # disclosed. The pipeline can't toggle it for a manual upload, so the
    # reminder is a hard gate in the report the operator reads.
    gate(
        "ai_disclosure_reminder", True,
        "REQUIRED: enable 'Altered content / synthetic media' on upload "
        "(automatic when uploading via pipeline.upload)",
    )

    return {"passed": all(c["ok"] for c in checks), "checks": checks}
