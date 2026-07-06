# Faceless Nostalgia Video Pipeline

Turns a text script into an upload-ready YouTube video — voiceover, visuals,
Ken Burns motion, thumbnail, metadata, and quality checks — with one command.
Built for a **faceless nostalgia channel aimed at US viewers aged 55+**
(vanished places, old objects, warm memories, optional faith/community themes),
because that audience is the highest-value one for advertisers and the niche
has low production cost per video.

Runs **free with no API keys** for the core pipeline (voiceover via Microsoft
`edge-tts`, images from Openverse/Wikimedia public domain, assembly via
`ffmpeg`). Claude script generation and YouTube upload are optional add-ons.

## What it does

```
script.md ──▶ voiceover (edge-tts) ──▶ public-domain images (Openverse/Wikimedia)
          ──▶ ffmpeg Ken Burns clips ──▶ concat + music + loudnorm ──▶ final.mp4
          ──▶ thumbnail.jpg ──▶ metadata.txt (+ image credits) ──▶ quality report
```

## Layout

```
pipeline/
  config.py         env-driven settings + Docker-secret reader
  script_parser.py  parses the script .md format into segments
  script_gen.py     optional: generate a script via a chosen LLM provider
  providers/        pluggable LLM backends: claude / gemini / qwen
  tts.py            edge-tts voiceover, one mp3 per segment
  visuals.py        Openverse + Wikimedia public-domain image search/download
  assemble.py       ffmpeg: Ken Burns clips -> concat -> music -> loudnorm
  thumbnail.py      Pillow: cover image + big high-contrast title text
  checks.py         quality gates (duration, streams, AI-disclosure reminder)
  upload.py         optional: YouTube Data API v3 upload (private draft)
  main.py           CLI entrypoint
scripts/            your script files (ep01 sample included)
tests/              parser unit tests
Dockerfile          ffmpeg + fonts + python, non-root
docker-compose.yml  volumes for output/assets/scripts/secrets, Claude secret
```

## Script format

One file per video. `# TITLE:` is required; the rest are optional.

```
# TITLE: 7 Places From Your Childhood That No Longer Exist
# DESCRIPTION: Remember the mall fountain? ... #nostalgia #1970s
# TAGS: nostalgia, 1970s, vanished america, remember when
# THUMBNAIL_TEXT: GONE FOREVER

[IMAGE: 1960s woolworth lunch counter interior black and white]
Narration spoken over this image...

[IMAGE: drive-in movie theater at dusk 1970s]
Next segment's narration...
```

Each `[IMAGE: ...]` starts a segment. The narration under it is voiced, and its
image is shown for exactly that narration's length. A ready sample episode is in
`scripts/ep01_vanished_places.md`.

## Quick start (free, no keys)

```bash
cp .env.example .env
pip install edge-tts httpx Pillow      # core deps only
python -m pipeline.main --script scripts/ep01_vanished_places.md
```

Output lands in `output/<slug>-<timestamp>/`: `final.mp4`, `thumbnail.jpg`,
`metadata.txt` (with image credits), `report.json`. `ffmpeg` must be installed
(`apt install ffmpeg` / `brew install ffmpeg`), or just use Docker below.

### Docker

```bash
docker compose build
docker compose run --rm video --script scripts/ep01_vanished_places.md
# results appear in ./output/
```

## Optional: generate a script with an AI (Claude / Gemini / Qwen)

Pick the writer with `SCRIPT_PROVIDER` and install only that provider's SDK.
Each provider uses its own official SDK and its own API key:

| `SCRIPT_PROVIDER` | SDK (`pip install`) | Key env var / secret file | Model env var (default) |
|---|---|---|---|
| `claude` | `anthropic` | `ANTHROPIC_API_KEY` / `secrets/anthropic_api_key` | `CLAUDE_MODEL` (`claude-opus-4-8`) |
| `gemini` | `google-genai` | `GEMINI_API_KEY` / `secrets/gemini_api_key` | `GEMINI_MODEL` (`gemini-2.5-flash`) |
| `qwen` | `openai` | `DASHSCOPE_API_KEY` / `secrets/dashscope_api_key` | `QWEN_MODEL` (`qwen-plus`) |

Qwen is reached through Alibaba DashScope's OpenAI-compatible endpoint (hence
the `openai` SDK); switch region with `QWEN_BASE_URL`.

```bash
mkdir -p secrets && umask 077
# put the key for the provider you chose, e.g. Gemini:
printf '%s' 'AIza...' > secrets/gemini_api_key

SCRIPT_PROVIDER=gemini python -m pipeline.main \
    --topic "sounds from the 1970s that disappeared"
```

The generated script is saved to `scripts/generated/` **for you to review and
edit before rendering** — treat it as a draft, not a finished product. Switch
provider any time by changing `SCRIPT_PROVIDER`; the rest of the pipeline is
identical.

> **Docker note:** `docker-compose.yml` mounts a secret file per provider. For
> providers you don't use, create empty placeholders so Compose doesn't error:
> `touch secrets/anthropic_api_key secrets/gemini_api_key secrets/dashscope_api_key`.

## Optional: upload to YouTube

The pipeline uploads as a **PRIVATE draft** so you always review before
publishing, and always sets the synthetic-media disclosure flag (required by
YouTube's 2026 AI policy).

1. Google Cloud Console → new project → enable **YouTube Data API v3**.
2. OAuth consent screen → add your channel's Google account as a **test user**.
3. Credentials → **OAuth client ID (Desktop app)** → download to
   `secrets/client_secret.json`.
4. `pip install google-api-python-client google-auth-oauthlib`
5. `python -m pipeline.main --script scripts/ep01_vanished_places.md --upload`
   (first run opens a browser for consent; token is cached in `secrets/`).

## Honest notes on making this pay

- **Target US/UK/CA/AU English audiences.** YouTube shows no ads to viewers in
  Russia, so a Russian-language channel earns almost nothing — the English
  script + US neural voice is deliberate.
- **YouTube demonetizes low-effort mass-produced AI content.** This pipeline is
  built so each video carries a real, hand-checkable script and curated image
  queries — the "thin layer of genuine effort" that passes review. Don't run it
  as a spam farm; that gets the channel demonetized, not rich.
- **Monetization needs 1,000 subscribers + 4,000 watch hours.** That takes
  months of consistent uploads; most channels never reach it. The payoff is
  that a video keeps earning for years once it does.
- **Facts must be real and content must be kind.** The niche works because it's
  warm and honest. Never invent history, never fear-monger or push fake
  health/finance claims at older viewers — that's both wrong and a fast route
  to a channel strike.

Nothing here is a get-rich-quick guarantee. It's a tool that removes the
production grind so you can focus on picking good topics and posting
consistently.

## Tests

```bash
python -m pytest tests/ -q
```
