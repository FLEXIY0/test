FROM python:3.12-slim-bookworm

# ffmpeg for assembly; fonts for thumbnails; ca-certificates for HTTPS APIs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 creator

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY scripts/ ./scripts/

RUN mkdir -p /app/output /app/assets /app/secrets \
    && chown -R creator:creator /app/output /app/assets /app/secrets
VOLUME ["/app/output", "/app/assets", "/app/secrets"]

USER creator
ENV PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/app/output \
    ASSETS_DIR=/app/assets

# Default: render the bundled sample episode. Override the command to point at
# your own script, e.g.:  docker compose run --rm video --script scripts/my.md
ENTRYPOINT ["python", "-m", "pipeline.main"]
CMD ["--script", "scripts/ep01_vanished_places.md"]
