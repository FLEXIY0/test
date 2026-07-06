"""Minimal dependency-free HTTP health endpoint for Docker HEALTHCHECK.

GET /healthz returns 200 with a JSON body while every feed snapshot is
younger than the dead-feed threshold, 503 otherwise. Uses raw asyncio so no
web framework enters the attack surface.
"""

from __future__ import annotations

import asyncio
import json
import time

from .feeds import BookStore

DEAD_FEED_MS = 60_000.0


class HealthServer:
    def __init__(self, store: BookStore, port: int, started_at: float | None = None) -> None:
        self.store = store
        self.port = port
        self.started_at = started_at or time.time()
        self.extra_status: dict = {}   # main loop injects pnl / trade counters

    def _payload(self) -> tuple[int, dict]:
        staleness = self.store.staleness_report()
        feeds_ok = bool(staleness) and all(age < DEAD_FEED_MS for age in staleness.values())
        body = {
            "status": "ok" if feeds_ok else "degraded",
            "uptime_s": round(time.time() - self.started_at, 1),
            "feed_age_ms": staleness,
            **self.extra_status,
        }
        return (200 if feeds_ok else 503), body

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            # Drain remaining headers.
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break
            path = request_line.split(b" ")[1].decode() if b" " in request_line else "/"
            if path.startswith("/healthz") or path == "/":
                code, body = self._payload()
            else:
                code, body = 404, {"error": "not found"}
            raw = json.dumps(body, separators=(",", ":")).encode()
            status_text = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}[code]
            writer.write(
                f"HTTP/1.1 {code} {status_text}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(raw)}\r\n"
                f"Connection: close\r\n\r\n".encode() + raw
            )
            await writer.drain()
        except Exception:  # noqa: BLE001 — never let a probe kill the server
            pass
        finally:
            writer.close()

    async def run_forever(self) -> None:
        server = await asyncio.start_server(self._handle, host="0.0.0.0", port=self.port)
        async with server:
            await server.serve_forever()
