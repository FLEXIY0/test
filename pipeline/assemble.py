"""ffmpeg assembly: per-segment Ken Burns clips -> concat -> music + loudnorm.

Every step shells out to ffmpeg/ffprobe (installed in the Docker image), so
there are no fragile Python video dependencies.
"""

from __future__ import annotations

import logging
import os
import subprocess

from .config import Settings

logger = logging.getLogger("assemble")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd[:6])}...\n"
            f"stderr tail: {proc.stderr[-800:]}"
        )


def probe_duration(path: str) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr[-300:]}")
    return float(proc.stdout.strip())


def render_segment(
    image: str, audio: str, out_path: str, settings: Settings
) -> float:
    """One still image + one narration mp3 -> a Ken Burns video clip."""
    duration = probe_duration(audio)
    frames = max(int(duration * settings.fps) + 1, settings.fps)
    w, h = settings.width, settings.height
    # Oversample before zoompan to avoid jitter, slow push-in capped at 1.15x.
    vf = (
        f"scale={w * 4 // 3}:{h * 4 // 3}:force_original_aspect_ratio=increase,"
        f"crop={w * 4 // 3}:{h * 4 // 3},"
        f"zoompan=z='min(zoom+0.0006,1.15)'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={w}x{h}:fps={settings.fps},"
        f"format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", image, "-i", audio,
        "-vf", vf, "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-shortest", out_path,
    ])
    return duration


def concat_segments(segment_paths: list[str], out_path: str, workdir: str) -> None:
    list_path = os.path.join(workdir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for path in segment_paths:
            fh.write(f"file '{os.path.abspath(path)}'\n")
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", out_path,
    ])


def finalize(video_in: str, out_path: str, settings: Settings) -> None:
    """Add looped background music (if configured) and normalize loudness to
    the YouTube-standard -14 LUFS. Video stream is copied, not re-encoded."""
    loudnorm = "loudnorm=I=-14:TP=-1.5:LRA=11"
    if settings.music_file and os.path.exists(settings.music_file):
        _run([
            "ffmpeg", "-y", "-i", video_in,
            "-stream_loop", "-1", "-i", settings.music_file,
            "-filter_complex",
            f"[1:a]volume={settings.music_volume}[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=3,{loudnorm}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            out_path,
        ])
    else:
        if settings.music_file:
            logger.warning("MUSIC_FILE '%s' not found — skipping music", settings.music_file)
        _run([
            "ffmpeg", "-y", "-i", video_in,
            "-af", loudnorm,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            out_path,
        ])


def assemble_video(
    images: list[str], audios: list[str], workdir: str, out_path: str, settings: Settings
) -> float:
    """Full assembly; returns total duration in seconds."""
    if len(images) != len(audios):
        raise ValueError(f"images ({len(images)}) != audio segments ({len(audios)})")
    total = 0.0
    clip_paths = []
    for i, (image, audio) in enumerate(zip(images, audios)):
        clip = os.path.join(workdir, f"clip_{i:03d}.mp4")
        total += render_segment(image, audio, clip, settings)
        clip_paths.append(clip)
        logger.info("rendered clip %d/%d", i + 1, len(images))
    merged = os.path.join(workdir, "merged.mp4")
    concat_segments(clip_paths, merged, workdir)
    finalize(merged, out_path, settings)
    return total
