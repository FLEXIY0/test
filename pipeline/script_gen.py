"""Optional script generation via the Claude Messages API.

Works only when ANTHROPIC_API_KEY is configured; without it the pipeline uses
ready-made script files from scripts/. The generated script comes back in the
exact format that pipeline.script_parser understands.
"""

from __future__ import annotations

import logging

from .config import Settings
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

Output MUST follow this exact format and nothing else:

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


def generate_script(topic: str, settings: Settings) -> ScriptDoc:
    """Generate a parsed script for `topic`. Raises RuntimeError on failure."""
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either configure it or pass a "
            "ready-made script file with --script scripts/<name>.md"
        )

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        with client.messages.stream(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Write the full video script for this topic: {topic}",
                }
            ],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError as exc:
        raise RuntimeError(f"Claude API rate limit hit; retry later: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(f"Network error reaching the Claude API: {exc}") from exc

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined this topic; pick a different one.")

    raw = "".join(block.text for block in message.content if block.type == "text")
    try:
        doc = parse_script(raw)
    except ScriptFormatError as exc:
        raise RuntimeError(f"generated script failed to parse: {exc}") from exc

    logger.info(
        "generated script '%s': %d segments, %d words (tokens in=%d out=%d)",
        doc.title, len(doc.segments), doc.word_count,
        message.usage.input_tokens, message.usage.output_tokens,
    )
    return doc
