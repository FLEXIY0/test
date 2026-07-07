"""ffmpeg assembly: multi-image Ken Burns segments with crossfades.

Each narration segment now shows several stills. Camera motion alternates
between four variants (zoom in / zoom out / pan left / pan right) so a long
video never feels static, and stills within a segment are joined with a short
crossfade. Segments are concatenated with straight cuts (natural at narration
boundaries), then music and -14 LUFS loudness normalization are applied.
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


def _motion_filter(variant: int, frames: int, settings: Settings) -> str:
    """One of four Ken Burns variants. `variant` cycles globally so adjacent
    stills always move differently."""
    w, h = settings.width, settings.height
    ow, oh = w * 4 // 3, h * 4 // 3   # oversample to avoid zoompan jitter
    base = (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
        f"crop={ow}:{oh},"
    )
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    variants = [
        # slow push-in
        f"zoompan=z='min(zoom+0.0008,1.18)':{center}",
        # slow pull-out
        f"zoompan=z='if(lte(on,1),1.18,max(1.001,zoom-0.0008))':{center}",
        # pan left -> right at fixed zoom
        f"zoompan=z='1.12':x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'",
        # pan right -> left at fixed zoom
        f"zoompan=z='1.12':x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'",
    ]
    motion = variants[variant % len(variants)]
    return (
        f"{base}{motion}:d={frames}:s={w}x{h}:fps={settings.fps},"
        f"format=yuv420p,setsar=1"
    )


def render_segment(
    images: list[str], audio: str, out_path: str,
    settings: Settings, motion_offset: int,
) -> float:
    """Several stills + one narration mp3 -> one video clip.

    Stills split the narration time evenly and crossfade into each other;
    total video length equals the audio length exactly.
    """
    duration = probe_duration(audio)
    n = len(images)
    fade = settings.xfade_s if n > 1 else 0.0
    # xfade overlaps consume fade seconds per joint, so each clip must be
    # slightly longer than duration/n for the joined result to equal duration.
    clip_d = (duration + fade * (n - 1)) / n
    clip_d = max(clip_d, fade + 0.5)
    frames = max(int(clip_d * settings.fps) + 1, settings.fps)

    cmd: list[str] = ["ffmpeg", "-y"]
    for image in images:
        cmd += ["-loop", "1", "-t", f"{clip_d:.3f}", "-i", image]
    cmd += ["-i", audio]

    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]{_motion_filter(motion_offset + i, frames, settings)}[v{i}]")
    if n == 1:
        last = "[v0]"
    else:
        prev = "[v0]"
        for i in range(1, n):
            offset = i * (clip_d - fade)
            out_label = f"[x{i}]"
            parts.append(
                f"{prev}[v{i}]xfade=transition=fade:duration={fade:.3f}"
                f":offset={offset:.3f}{out_label}"
            )
            prev = out_label
        last = prev

    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", last, "-map", f"{n}:a",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        out_path,
    ]
    _run(cmd)
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
    image_sets: list[list[str]], audios: list[str],
    workdir: str, out_path: str, settings: Settings,
) -> tuple[float, list[float]]:
    """Full assembly. Returns (total duration, per-segment durations) — the
    per-segment durations feed subtitle timestamp offsets."""
    if len(image_sets) != len(audios):
        raise ValueError(f"image sets ({len(image_sets)}) != audio segments ({len(audios)})")
    durations: list[float] = []
    clip_paths = []
    motion_offset = 0
    for i, (images, audio) in enumerate(zip(image_sets, audios)):
        clip = os.path.join(workdir, f"clip_{i:03d}.mp4")
        durations.append(
            render_segment(images, audio, clip, settings, motion_offset)
        )
        motion_offset += len(images)   # keep the motion variety rolling
        clip_paths.append(clip)
        logger.info("rendered clip %d/%d (%d stills)", i + 1, len(image_sets), len(images))
    merged = os.path.join(workdir, "merged.mp4")
    concat_segments(clip_paths, merged, workdir)
    finalize(merged, out_path, settings)
    return sum(durations), durations
