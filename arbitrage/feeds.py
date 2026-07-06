"""WebSocket order-book feeds with supervised reconnection.

One asyncio task per (exchange, symbol). Each task runs ``watch_order_book``
in a loop; any exception triggers exponential back-off with jitter and a full
reconnect. The freshest snapshot per (exchange, symbol) lives in
:class:`BookStore`, which the decision engine reads lock-free (asyncio is
single-threaded; assignment of a whole snapshot is atomic).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

import ccxt.pro as ccxtpro

from .config import ExchangeConfig, Settings
from .logging_setup import log_event
from .models import BookSnapshot

logger = logging.getLogger("feeds")


class BookStore:
    """Latest order-book snapshots keyed by (exchange_id, symbol)."""

    def __init__(self) -> None:
        self._books: dict[tuple[str, str], BookSnapshot] = {}
        self.updated = asyncio.Event()   # pulsed on every book update

    def put(self, snap: BookSnapshot) -> None:
        self._books[(snap.exchange_id, snap.symbol)] = snap
        self.updated.set()

    def get(self, exchange_id: str, symbol: str) -> BookSnapshot | None:
        return self._books.get((exchange_id, symbol))

    def staleness_report(self) -> dict[str, float]:
        """Map "exchange:symbol" -> age in ms, for the health endpoint."""
        return {f"{k[0]}:{k[1]}": round(v.age_ms(), 1) for k, v in self._books.items()}


def build_exchange(cfg: ExchangeConfig) -> ccxtpro.Exchange:
    """Instantiate a ccxt.pro client with rate limiting enabled."""
    klass = getattr(ccxtpro, cfg.exchange_id)
    params: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    }
    if cfg.api_key:
        params["apiKey"] = cfg.api_key
    if cfg.api_secret:
        params["secret"] = cfg.api_secret
    if cfg.api_password:
        params["password"] = cfg.api_password
    return klass(params)


class Backoff:
    """Exponential back-off with decorrelated jitter and stability reset."""

    def __init__(self, base_s: float, cap_s: float, reset_after_s: float = 60.0) -> None:
        self.base_s = base_s
        self.cap_s = cap_s
        self.reset_after_s = reset_after_s
        self._attempt = 0
        self._last_ok = time.monotonic()

    def record_success(self) -> None:
        if time.monotonic() - self._last_ok > self.reset_after_s:
            self._attempt = 0
        self._last_ok = time.monotonic()

    async def sleep(self) -> float:
        delay = min(self.base_s * (2 ** self._attempt), self.cap_s)
        delay *= random.uniform(0.5, 1.0)
        self._attempt += 1
        await asyncio.sleep(delay)
        return delay


async def orderbook_feed(
    exchange: ccxtpro.Exchange,
    symbol: str,
    store: BookStore,
    settings: Settings,
) -> None:
    """Supervised feed loop for one (exchange, symbol). Never returns."""
    backoff = Backoff(settings.backoff_base_s, settings.backoff_cap_s)
    while True:
        try:
            book = await exchange.watch_order_book(symbol, limit=settings.book_depth)
            snap = BookSnapshot(
                exchange_id=exchange.id,
                symbol=symbol,
                bids=[(float(p), float(s)) for p, s, *_ in book["bids"][: settings.book_depth]],
                asks=[(float(p), float(s)) for p, s, *_ in book["asks"][: settings.book_depth]],
                ts_ms=time.time() * 1000.0,
            )
            store.put(snap)
            backoff.record_success()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — any WS failure means reconnect
            delay = await backoff.sleep()
            log_event(
                logger, logging.WARNING, "feed_reconnect",
                exchange=exchange.id, symbol=symbol,
                error=type(exc).__name__, detail=str(exc)[:300],
                backoff_s=round(delay, 2),
            )
