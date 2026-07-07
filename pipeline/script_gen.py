"""Script generation via a configurable LLM provider (Claude / Gemini / Qwen).

Selected with SCRIPT_PROVIDER; each provider uses its own official SDK and key.
Without a configured provider the pipeline still runs from ready-made script
files (scripts/). The generated script comes back in the exact format that
pipeline.script_parser understands.
"""

from __future__ import annotations

import logging

from .config import Settings
from .providers import ProviderError, get_provider
from .script_parser import ScriptDoc, ScriptFormatError, parse_script

logger = logging.getLogger("script_gen")

SYSTEM_PROMPT = """\
You write voiceover scripts for a faceless YouTube channel aimed at US viewers
aged 55+. The channel's theme: warm nostalgia about vanished places, objects,
and moments from 1950s-1980s America, optionally woven with gentle faith and
community memories (Sunday mornings, family dinners, small-town life).

Rules:
- Warm, sensory, second-person voice ("you remember the smell of..."). Never
  condescending, never political, never medical or financial advice.
- Factually accurate about real history. If unsure about a fact, keep it
  general rather than inventing specifics.
- Structure: a 15-second hook that paints a sensory memory, then 6-8 items,
  each 100-140 words with one vivid concrete detail, then a closing that asks
  viewers to comment with their own memory and to subscribe.
- Target 1100-1400 words total (about 9-11 minutes narrated).
- Image queries must describe era-accurate, searchable public-domain photo
  subjects (e.g. "1960s woolworth lunch counter interior black and white").

Output MUST follow this exact format and NOTHING else (no preamble, no code
fences, no closing remarks):

# TITLE: <compelling title under 90 chars, plain-spoken, curiosity-driven>
# DESCRIPTION: <2-3 sentences + a question inviting comments + 4-6 hashtags>
# TAGS: <10-14 comma-separated tags>
# THUMBNAIL_TEXT: <2-4 punchy words in caps>

[IMAGE: <photo search query for the hook>]
<hook narration>

[IMAGE: <photo search query>]
<item narration>

...repeat for every item and the closing.
"""


MAX_ATTEMPTS = 3


def generate_script(topic: str, settings: Settings) -> ScriptDoc:
    """Generate a parsed script for `topic`, retrying on format errors.

    A model occasionally breaks the output format; instead of dying, the
    request is retried with the parse error appended so the model can fix it.
    """
    provider = get_provider(settings)
    user = f"Write the full video script for this topic: {topic}"
    logger.info("generating script via %s (%s)", provider.name, provider.model)

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = provider.generate(SYSTEM_PROMPT, user)
        except ProviderError as exc:
            raise RuntimeError(str(exc)) from exc

        try:
            doc = parse_script(_strip_fences(raw))
        except ScriptFormatError as exc:
            last_error = exc
            logger.warning(
                "attempt %d/%d: %s output failed to parse (%s) — retrying",
                attempt, MAX_ATTEMPTS, provider.name, exc,
            )
            user = (
                f"Write the full video script for this topic: {topic}\n\n"
                f"IMPORTANT: your previous attempt was rejected by the parser "
                f"with this error: {exc}. Follow the output format EXACTLY as "
                f"specified — headers first, then [IMAGE: ...] segments, no "
                f"other text."
            )
            continue

        logger.info(
            "generated '%s' via %s: %d segments, %d words",
            doc.title, provider.name, len(doc.segments), doc.word_count,
        )
        return doc

    raise RuntimeError(
        f"{provider.name} failed to produce a parseable script after "
        f"{MAX_ATTEMPTS} attempts: {last_error}"
    )


def _strip_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if a model wrapped output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped
