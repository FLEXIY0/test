"""CLI entrypoint: one command produces one upload-ready video package.

Usage:
    # fully automatic
    python -m pipeline.main --script scripts/ep01_vanished_places.md

    # generate the script with the configured AI provider
    python -m pipeline.main --topic "sounds from the 1970s that disappeared"

    # human-in-the-loop image curation (recommended for quality):
    python -m pipeline.main --script scripts/ep01.md --review
    #   -> voices the script and downloads image candidates, then stops.
    #   Open output/<run>/work/candidates/, DELETE images you don't like, then:
    python -m pipeline.main --resume output/<run>

    # upload as a PRIVATE draft after checks pass
    python -m pipeline.main --script ... --upload

Output lands in output/<slug>-<timestamp>/:
    final.mp4, final.srt, thumbnail.jpg, metadata.txt, report.json, work/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time

from .assemble import assemble_video
from .checks import run_checks
from .config import Settings, load_settings
from .script_parser import ScriptDoc, parse_script_file
from .subtitles import write_srt
from .thumbnail import make_thumbnail
from .tts import run_tts
from .visuals import fetch_candidates, select_images

logger = logging.getLogger("main")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "video"


def write_metadata(doc: ScriptDoc, attributions: list[str], path: str) -> None:
    """Copy-paste-ready upload metadata, including required attributions."""
    lines = [
        f"TITLE:\n{doc.title}\n",
        f"DESCRIPTION:\n{doc.description}\n",
        "\nImage credits:",
        *[f"- {a}" for a in dict.fromkeys(attributions)],  # dedupe, keep order
        f"\nTAGS:\n{', '.join(doc.tags)}\n",
        "\nUPLOAD CHECKLIST:",
        "- [ ] Set 'Altered content / synthetic media' disclosure to YES",
        "- [ ] Audience: not made for kids",
        "- [ ] Attach final.srt as English subtitles",
        "- [ ] Watch the video start to finish before publishing",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def save_script_copy(doc: ScriptDoc, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# TITLE: {doc.title}\n# DESCRIPTION: {doc.description}\n"
                 f"# TAGS: {', '.join(doc.tags)}\n"
                 f"# THUMBNAIL_TEXT: {doc.thumbnail_text}\n\n")
        for seg in doc.segments:
            fh.write(f"[IMAGE: {seg.image_query}]\n{seg.text}\n\n")


def prepare(doc: ScriptDoc, run_dir: str, settings: Settings) -> tuple[list[str], list[list[dict]], str]:
    """Stages shared by fresh and resumed runs: voiceover + image candidates.
    Both are idempotent — existing files are reused on --resume."""
    workdir = os.path.join(run_dir, "work")
    os.makedirs(workdir, exist_ok=True)
    save_script_copy(doc, os.path.join(run_dir, "script.md"))

    logger.info("step 1/6: voiceover (%s)", settings.voice)
    audio_paths, segment_words = run_tts(doc, workdir, settings)

    candidates_dir = os.path.join(workdir, "candidates")
    if os.path.isdir(candidates_dir):
        logger.info("step 2/6: reusing existing image candidates")
    else:
        logger.info("step 2/6: downloading image candidates (LoC/Openverse/Wikimedia)")
        fetch_candidates([s.image_query for s in doc.segments], workdir, settings)
    return audio_paths, segment_words, candidates_dir


def produce(doc: ScriptDoc, run_dir: str, settings: Settings,
            review: bool, upload: bool) -> int:
    workdir = os.path.join(run_dir, "work")
    final_path = os.path.join(run_dir, "final.mp4")
    srt_path = os.path.join(run_dir, "final.srt")
    thumb_path = os.path.join(run_dir, "thumbnail.jpg")

    logger.info("script: '%s' — %d segments, %d words",
                doc.title, len(doc.segments), doc.word_count)
    audio_paths, segment_words, candidates_dir = prepare(doc, run_dir, settings)

    if review:
        logger.info(
            "REVIEW MODE — candidates are ready.\n"
            "  1. Open:   %s\n"
            "  2. DELETE the images you don't like in each seg_NNN folder\n"
            "     (add your own .jpg files there if you have better ones)\n"
            "  3. Continue with:  python -m pipeline.main --resume %s",
            candidates_dir, run_dir,
        )
        return 0

    logger.info("step 3/6: selecting up to %d images per segment", settings.images_per_segment)
    image_sets, attributions = select_images(candidates_dir, len(doc.segments), settings)

    logger.info("step 4/6: assembling video")
    total, durations = assemble_video(image_sets, audio_paths, workdir, final_path, settings)
    logger.info("assembled %.1f minutes", total / 60)

    logger.info("step 5/6: subtitles, thumbnail, metadata")
    cue_count = write_srt(segment_words, durations, srt_path)
    logger.info("wrote %d subtitle cues to %s", cue_count, os.path.basename(srt_path))
    make_thumbnail(image_sets[0][0], doc.thumbnail_text, thumb_path)
    write_metadata(doc, attributions, os.path.join(run_dir, "metadata.txt"))

    logger.info("step 6/6: quality checks")
    unique_images = len({p for images in image_sets for p in images})
    report = run_checks(final_path, doc, unique_images, settings)
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    for check in report["checks"]:
        logger.log(
            logging.INFO if check["ok"] else logging.ERROR,
            "  [%s] %s: %s", "PASS" if check["ok"] else "FAIL",
            check["name"], check["detail"],
        )

    if not report["passed"]:
        logger.error("quality gates FAILED — fix and re-run; not uploading")
        logger.info("output: %s", run_dir)
        return 1

    if upload:
        from .upload import upload_video
        video_id = upload_video(
            final_path, doc.title, doc.description, doc.tags, thumb_path,
            privacy="private",
        )
        logger.info("uploaded as PRIVATE draft: https://studio.youtube.com — "
                    "review and publish manually (video id %s)", video_id)

    logger.info("done: %s", run_dir)
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Generate one faceless video.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--script", help="path to a ready script .md file")
    source.add_argument("--topic", help="generate the script with the configured AI provider")
    source.add_argument("--resume", metavar="RUN_DIR",
                        help="continue a run after reviewing image candidates")
    parser.add_argument("--review", action="store_true",
                        help="stop after downloading image candidates for human curation")
    parser.add_argument("--upload", action="store_true",
                        help="upload to YouTube as a PRIVATE draft after checks pass")
    args = parser.parse_args()

    settings = load_settings()

    if args.resume:
        run_dir = args.resume.rstrip("/")
        script_copy = os.path.join(run_dir, "script.md")
        if not os.path.exists(script_copy):
            parser.error(f"{script_copy} not found — is {run_dir} a pipeline run dir?")
        doc = parse_script_file(script_copy)
        sys.exit(produce(doc, run_dir, settings, review=False, upload=args.upload))

    if args.script:
        doc = parse_script_file(args.script)
    else:
        from .script_gen import generate_script
        doc = generate_script(args.topic, settings)
        os.makedirs("scripts/generated", exist_ok=True)
        gen_path = f"scripts/generated/{slugify(doc.title)}.md"
        save_script_copy(doc, gen_path)
        logger.info("generated script saved to %s — review it before publishing!", gen_path)

    run_dir = os.path.join(
        settings.output_dir, f"{slugify(doc.title)}-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    sys.exit(produce(doc, run_dir, settings, review=args.review, upload=args.upload))


if __name__ == "__main__":
    main()
