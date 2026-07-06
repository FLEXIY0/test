"""CLI entrypoint: one command produces one upload-ready video package.

Usage:
    python -m pipeline.main --script scripts/ep01_vanished_places.md
    python -m pipeline.main --topic "sounds from the 1970s that disappeared"
    python -m pipeline.main --script ... --upload   # upload as PRIVATE draft

Output lands in output/<slug>-<timestamp>/:
    final.mp4, thumbnail.jpg, metadata.txt, report.json, work/ (intermediates)
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
from .thumbnail import make_thumbnail
from .tts import run_tts
from .visuals import fetch_images

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
        *[f"- {a}" for a in attributions],
        f"\nTAGS:\n{', '.join(doc.tags)}\n",
        "\nUPLOAD CHECKLIST:",
        "- [ ] Set 'Altered content / synthetic media' disclosure to YES",
        "- [ ] Audience: not made for kids",
        "- [ ] Watch the video start to finish before publishing",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def produce(doc: ScriptDoc, settings: Settings, upload: bool) -> int:
    run_dir = os.path.join(
        settings.output_dir, f"{slugify(doc.title)}-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    workdir = os.path.join(run_dir, "work")
    os.makedirs(workdir, exist_ok=True)
    final_path = os.path.join(run_dir, "final.mp4")
    thumb_path = os.path.join(run_dir, "thumbnail.jpg")

    logger.info("script: '%s' — %d segments, %d words",
                doc.title, len(doc.segments), doc.word_count)

    logger.info("step 1/5: voiceover (%s)", settings.voice)
    audio_paths = run_tts(doc, workdir, settings)

    logger.info("step 2/5: sourcing %d public-domain images", len(doc.segments))
    image_paths, attributions = fetch_images(
        [s.image_query for s in doc.segments], workdir
    )

    logger.info("step 3/5: assembling video")
    duration = assemble_video(image_paths, audio_paths, workdir, final_path, settings)
    logger.info("assembled %.1f minutes", duration / 60)

    logger.info("step 4/5: thumbnail + metadata")
    make_thumbnail(image_paths[0], doc.thumbnail_text, thumb_path)
    write_metadata(doc, attributions, os.path.join(run_dir, "metadata.txt"))

    logger.info("step 5/5: quality checks")
    report = run_checks(final_path, doc, len(set(image_paths)), settings)
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
    source.add_argument("--topic", help="generate the script with Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--upload", action="store_true",
                        help="upload to YouTube as a PRIVATE draft after checks pass")
    args = parser.parse_args()

    settings = load_settings()
    if args.script:
        doc = parse_script_file(args.script)
    else:
        from .script_gen import generate_script
        doc = generate_script(args.topic, settings)
        # Keep the generated script so it can be edited and re-rendered.
        os.makedirs("scripts/generated", exist_ok=True)
        gen_path = f"scripts/generated/{slugify(doc.title)}.md"
        with open(gen_path, "w", encoding="utf-8") as fh:
            fh.write(f"# TITLE: {doc.title}\n# DESCRIPTION: {doc.description}\n"
                     f"# TAGS: {', '.join(doc.tags)}\n"
                     f"# THUMBNAIL_TEXT: {doc.thumbnail_text}\n\n")
            for seg in doc.segments:
                fh.write(f"[IMAGE: {seg.image_query}]\n{seg.text}\n\n")
        logger.info("generated script saved to %s — review it before rendering!", gen_path)

    sys.exit(produce(doc, settings, upload=args.upload))


if __name__ == "__main__":
    main()
