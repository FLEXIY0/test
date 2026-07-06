"""Parser for the pipeline's script file format.

Format (markdown-ish, one file per video):

    # TITLE: 7 Places From Your Childhood That No Longer Exist
    # DESCRIPTION: Remember the mall fountain? ...
    # TAGS: nostalgia, 1960s, vanished america
    # THUMBNAIL_TEXT: GONE FOREVER

    [IMAGE: 1960s woolworth lunch counter interior]
    Narration paragraph for this visual...

    [IMAGE: drive-in movie theater at dusk 1965]
    Next paragraph...

Each [IMAGE: ...] marker starts a segment; the text until the next marker is
narrated over that image. Headers are optional except TITLE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(r"^#\s*(TITLE|DESCRIPTION|TAGS|THUMBNAIL_TEXT)\s*:\s*(.+)$")
IMAGE_RE = re.compile(r"^\[IMAGE:\s*(.+?)\s*\]$")


@dataclass
class Segment:
    image_query: str
    text: str


@dataclass
class ScriptDoc:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    thumbnail_text: str = ""
    segments: list[Segment] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())


class ScriptFormatError(ValueError):
    pass


def parse_script(raw: str) -> ScriptDoc:
    headers: dict[str, str] = {}
    segments: list[Segment] = []
    current_query: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_query, current_lines
        if current_query is not None:
            text = "\n".join(current_lines).strip()
            if text:
                segments.append(Segment(image_query=current_query, text=text))
        current_query, current_lines = None, []

    for line in raw.splitlines():
        stripped = line.strip()
        header = HEADER_RE.match(stripped)
        if header and current_query is None:
            headers[header.group(1)] = header.group(2).strip()
            continue
        image = IMAGE_RE.match(stripped)
        if image:
            flush()
            current_query = image.group(1)
            continue
        if current_query is not None:
            current_lines.append(line)
    flush()

    if "TITLE" not in headers:
        raise ScriptFormatError("script is missing '# TITLE:' header")
    if not segments:
        raise ScriptFormatError("script has no [IMAGE: ...] segments")

    tags = [t.strip() for t in headers.get("TAGS", "").split(",") if t.strip()]
    return ScriptDoc(
        title=headers["TITLE"],
        description=headers.get("DESCRIPTION", ""),
        tags=tags,
        thumbnail_text=headers.get("THUMBNAIL_TEXT", ""),
        segments=segments,
    )


def parse_script_file(path: str) -> ScriptDoc:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_script(fh.read())
