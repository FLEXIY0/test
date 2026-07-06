"""Public-domain / CC image sourcing via the Openverse and Wikimedia APIs.

Both APIs are free and keyless. Only permissive licenses are requested
(public domain, CC0, CC-BY); CC-BY attributions are collected so the caller
can paste them into the video description — required for monetized use.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("visuals")

USER_AGENT = "faceless-video-pipeline/1.0 (personal educational project)"
OPENVERSE_URL = "https://api.openverse.org/v1/images/"
WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"
ALLOWED_LICENSES = "cc0,pdm,by"


def _search_openverse(client: httpx.Client, query: str) -> dict | None:
    resp = client.get(
        OPENVERSE_URL,
        params={
            "q": query,
            "license": ALLOWED_LICENSES,
            "per_page": 5,
            "filter_dead": "true",
        },
    )
    resp.raise_for_status()
    for item in resp.json().get("results", []):
        if item.get("url"):
            return {
                "url": item["url"],
                "attribution": item.get("attribution")
                or f"\"{item.get('title', 'image')}\" by {item.get('creator', 'unknown')} "
                   f"({item.get('license', '')}) — {item.get('foreign_landing_url', '')}",
                "license": item.get("license", ""),
                "source": "openverse",
            }
    return None


def _search_wikimedia(client: httpx.Client, query: str) -> dict | None:
    resp = client.get(
        WIKIMEDIA_URL,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 5,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
        },
    )
    resp.raise_for_status()
    pages = (resp.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if infos and infos[0].get("url"):
            meta = infos[0].get("extmetadata") or {}
            license_name = (meta.get("LicenseShortName") or {}).get("value", "")
            artist = (meta.get("Artist") or {}).get("value", "")
            return {
                "url": infos[0]["url"],
                "attribution": f"{page.get('title', '')} ({license_name}) {artist} "
                               f"— via Wikimedia Commons",
                "license": license_name,
                "source": "wikimedia",
            }
    return None


def _download(client: httpx.Client, url: str, out_path: str) -> None:
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)


def fetch_images(queries: list[str], workdir: str) -> tuple[list[str], list[str]]:
    """Fetch one image per query. Returns (image_paths, attribution_lines).

    A query that finds nothing reuses the previous image so the video always
    assembles; the gap is reported so the operator can swap the image later.
    """
    paths: list[str] = []
    attributions: list[str] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True
    ) as client:
        for i, query in enumerate(queries):
            result = None
            for search in (_search_openverse, _search_wikimedia):
                try:
                    result = search(client, query)
                except httpx.HTTPError as exc:
                    logger.warning("%s failed for '%s': %s", search.__name__, query, exc)
                if result:
                    break
            out_path = os.path.join(workdir, f"img_{i:03d}.img")
            if result:
                try:
                    _download(client, result["url"], out_path)
                    paths.append(out_path)
                    attributions.append(result["attribution"])
                    logger.info("image %d/%d from %s", i + 1, len(queries), result["source"])
                    continue
                except httpx.HTTPError as exc:
                    logger.warning("download failed for '%s': %s", query, exc)
            if paths:
                logger.warning("no image for '%s' — reusing previous", query)
                paths.append(paths[-1])
                attributions.append(f"(missing image for: {query})")
            else:
                raise RuntimeError(
                    f"could not source any image for the first query '{query}' — "
                    "check network access to api.openverse.org / commons.wikimedia.org"
                )
    return paths, attributions
