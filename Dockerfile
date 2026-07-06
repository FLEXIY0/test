# --- build stage: resolve dependencies into a clean prefix -----------------
FROM python:3.12-slim-bookworm AS build

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- runtime stage: minimal, non-root, read-only friendly ------------------
FROM python:3.12-slim-bookworm

# curl only for the HEALTHCHECK probe
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 arbbot

COPY --from=build /install /usr/local
WORKDIR /app
COPY arbitrage/ ./arbitrage/

# Writable journal/state volume; everything else can be mounted read-only.
RUN mkdir -p /app/state && chown -R arbbot:arbbot /app/state
VOLUME ["/app/state"]

USER arbbot
ENV PYTHONUNBUFFERED=1 \
    STATE_DIR=/app/state \
    HEALTH_PORT=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["python", "-m", "arbitrage.main"]
