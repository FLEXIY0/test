"""Candidate-based public-domain image sourcing.

Sources, in priority order:
1. Library of Congress (loc.gov) — the deepest archive of exactly the era this
   channel covers; photo results are overwhelmingly public domain.
2. Openverse — CC0 / public domain / CC-BY aggregate.
3. Wikimedia Commons — fallback.

For every [IMAGE: ...] query the pipeline downloads several candidates into
    work/candidates/seg_000/00_loc.jpg, 01_openverse.jpg, ...
plus an attributions.json next to them. Selection then either happens
automatically (largest resolution wins) or after a human review pass in which
the operator simply deletes the images they don't like (--review / --resume).
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx
from PIL import Image

from .config import Settings

logger = logging.getLogger("visuals")

# Wikimedia's UA policy wants a descriptive agent with a contact; set your own
# email via IMAGE_UA_CONTACT for reliable Commons access.
USER_AGENT = (
    "faceless-video-pipeline/1.0 "
    f"({os.environ.get('IMAGE_UA_CONTACT', 'https://github.com/faceless-pipeline')}) "
    "httpx"
)
LOC_URL = "https://www.loc.gov/photos/"
OPENVERSE_URL = "https://api.openverse.org/v1/images/"
WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSES = "cc0,pdm,by"

ATTRIBUTIONS_FILE = "attributions.json"
LOC_DIM_RE = re.compile(r"#h=(\d+)&w=(\d+)")


def _search_loc(client: httpx.Client, query: str, limit: int) -> list[dict]:
    # at=results trims the response to just the result list — the full LoC
    # payload includes facets/pagination blobs big enough to hit read timeouts.
    resp = client.get(
        LOC_URL,
        params={"q": query, "fo": "json", "c": limit * 2, "at": "results"},
    )
    resp.raise_for_status()
    out = []
    for item in resp.json().get("results", []):
        urls = item.get("image_url") or []
        if not urls:
            continue
        best = urls[-1]  # LoC lists sizes small -> large
        width = 0
        match = LOC_DIM_RE.search(best)
        if match:
            width = int(match.group(2))
        out.append({
            "url": best,
            "hint_width": width,
            "attribution": f"\"{item.get('title', 'photo')}\" — Library of Congress, "
                           f"{item.get('url', '')}",
            "source": "loc",
        })
        if len(out) >= limit:
            break
    return out


def _search_openverse(client: httpx.Client, query: str, limit: int) -> list[dict]:
    resp = client.get(
        OPENVERSE_URL,
        params={"q": query, "license": ALLOWED_LICENSES,
                "per_page": max(limit, 5), "filter_dead": "true"},
    )
    resp.raise_for_status()
    out = []
    for item in resp.json().get("results", []):
        if not item.get("url"):
            continue
        out.append({
            "url": item["url"],
            "hint_width": int(item.get("width") or 0),
            "attribution": item.get("attribution")
            or f"\"{item.get('title', 'image')}\" by {item.get('creator', 'unknown')} "
               f"({item.get('license', '')}) — {item.get('foreign_landing_url', '')}",
            "source": "openverse",
        })
        if len(out) >= limit:
            break
    return out


def _search_wikimedia(client: httpx.Client, query: str, limit: int) -> list[dict]:
    resp = client.get(
        WIKIMEDIA_URL,
        params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": limit * 2, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
        },
    )
    resp.raise_for_status()
    pages = (resp.json().get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos or not infos[0].get("url"):
            continue
        info = infos[0]
        meta = info.get("extmetadata") or {}
        license_name = (meta.get("LicenseShortName") or {}).get("value", "")
        out.append({
            "url": info["url"],
            "hint_width": int(info.get("width") or 0),
            "attribution": f"{page.get('title', '')} ({license_name}) — via Wikimedia Commons",
            "source": "wikimedia",
        })
        if len(out) >= limit:
            break
    return out


def _download_verified(
    client: httpx.Client, url: str, out_path: str, min_width: int
) -> bool:
    """Download and keep only images that open cleanly and are wide enough."""
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        with Image.open(out_path) as img:
            img.load()
            if img.width < min_width:
                os.remove(out_path)
                return False
        return True
    except Exception as exc:  # noqa: BLE001 — any bad candidate is just skipped
        logger.debug("candidate rejected (%s): %s", url[:80], exc)
        if os.path.exists(out_path):
            os.remove(out_path)
        return False


def fetch_candidates(
    queries: list[str], workdir: str, settings: Settings
) -> str:
    """Download candidate images per query into candidates/seg_NNN/.

    Returns the candidates directory path. Attribution for every kept file is
    written to candidates/attributions.json as {"seg_000/00_loc.jpg": "..."}.
    """
    root = os.path.join(workdir, "candidates")
    attributions: dict[str, str] = {}
    timeout = httpx.Timeout(30.0, read=90.0)   # archive APIs can be slow readers
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
    ) as client:
        for i, query in enumerate(queries):
            seg_dir = os.path.join(root, f"seg_{i:03d}")
            os.makedirs(seg_dir, exist_ok=True)
            kept = 0
            for search in (_search_loc, _search_openverse, _search_wikimedia):
                if kept >= settings.candidates_per_query:
                    break
                try:
                    found = search(client, query, settings.candidates_per_query)
                except httpx.HTTPError as exc:
                    logger.warning("%s failed for '%s': %s", search.__name__, query, exc)
                    continue
                for cand in found:
                    if kept >= settings.candidates_per_query:
                        break
                    if cand["hint_width"] and cand["hint_width"] < settings.min_image_width:
                        continue
                    name = f"{kept:02d}_{cand['source']}.jpg"
                    if _download_verified(
                        client, cand["url"], os.path.join(seg_dir, name),
                        settings.min_image_width,
                    ):
                        attributions[f"seg_{i:03d}/{name}"] = cand["attribution"]
                        kept += 1
            logger.info("segment %d/%d ('%s'): %d candidates",
                        i + 1, len(queries), query[:50], kept)
    with open(os.path.join(root, ATTRIBUTIONS_FILE), "w", encoding="utf-8") as fh:
        json.dump(attributions, fh, indent=2, ensure_ascii=False)
    return root


def _image_area(path: str) -> int:
    try:
        with Image.open(path) as img:
            return img.width * img.height
    except Exception:  # noqa: BLE001
        return 0


def select_images(
    candidates_dir: str, num_segments: int, settings: Settings
) -> tuple[list[list[str]], list[str]]:
    """Pick up to images_per_segment files from each seg dir (largest first).

    Works identically before and after a human review pass: reviewing simply
    means deleting unwanted files, so whatever remains is what gets picked.
    Empty segments fall back to the previous segment's first image.
    Returns (per-segment image path lists, attribution lines for used images).
    """
    attr_path = os.path.join(candidates_dir, ATTRIBUTIONS_FILE)
    attributions: dict[str, str] = {}
    if os.path.exists(attr_path):
        with open(attr_path, "r", encoding="utf-8") as fh:
            attributions = json.load(fh)

    selected: list[list[str]] = []
    used_attributions: list[str] = []
    for i in range(num_segments):
        seg_key = f"seg_{i:03d}"
        seg_dir = os.path.join(candidates_dir, seg_key)
        files = []
        if os.path.isdir(seg_dir):
            files = [
                os.path.join(seg_dir, f)
                for f in sorted(os.listdir(seg_dir))
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            ]
        files.sort(key=_image_area, reverse=True)
        picked = files[: settings.images_per_segment]
        if not picked:
            if not selected:
                raise RuntimeError(
                    f"no usable image for the first segment ({seg_dir}) — "
                    "add an image file there and re-run with --resume"
                )
            logger.warning("segment %d has no images — reusing previous", i)
            picked = [selected[-1][0]]
            used_attributions.append(f"(missing image for segment {i})")
        else:
            for path in picked:
                rel = f"{seg_key}/{os.path.basename(path)}"
                used_attributions.append(attributions.get(rel, rel))
        selected.append(picked)
    return selected, used_attributions
