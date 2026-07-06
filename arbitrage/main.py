"""Daemon entrypoint: wires feeds → engine → executor → rebalancer.

Run with:  python -m arbitrage.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from .config import Settings, load_settings
from .engine import SpreadScanner
from .executor import Executor
from .feeds import BookStore, build_exchange, orderbook_feed
from .health import HealthServer
from .logging_setup import log_event, setup_logging
from .rebalancer import Rebalancer

logger = logging.getLogger("main")


async def decision_loop(
    settings: Settings,
    store: BookStore,
    scanner: SpreadScanner,
    executor: Executor,
    health: HealthServer,
) -> None:
    """Event-driven scan: wakes on every book update, prices all venue pairs,
    hands the single best opportunity to the executor."""
    exchange_ids = [cfg.exchange_id for cfg in settings.exchanges]
    while True:
        await store.updated.wait()
        store.updated.clear()
        books = {
            (ex_id, symbol): store.get(ex_id, symbol)
            for ex_id in exchange_ids
            for symbol in settings.symbols
        }
        opportunities = scanner.scan(books)
        if opportunities and not executor.in_cooldown():
            await executor.execute(opportunities[0])
        health.extra_status = {
            "dry_run": settings.dry_run,
            "trades": executor.trade_count,
            "cumulative_pnl_quote": round(executor.cumulative_pnl_quote, 6),
        }


async def supervise(name: str, coro_factory, settings: Settings) -> None:
    """Restart a component forever with exponential back-off on crash."""
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            await coro_factory()
            return  # clean exit (only on shutdown)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() - started > 60.0:
                attempt = 0  # ran stably before dying; reset the ladder
            delay = min(settings.backoff_base_s * (2 ** attempt), settings.backoff_cap_s)
            attempt += 1
            log_event(
                logger, logging.ERROR, "component_crashed",
                component=name, error=type(exc).__name__,
                detail=str(exc)[:300], restart_in_s=round(delay, 2),
            )
            await asyncio.sleep(delay)


async def run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    if len(settings.exchanges) < 2:
        raise SystemExit(
            "Need at least 2 exchanges in EXCHANGES for cross-exchange arbitrage "
            "(e.g. EXCHANGES=binance,kraken)."
        )

    log_event(
        logger, logging.INFO, "startup",
        exchanges=[c.exchange_id for c in settings.exchanges],
        symbols=list(settings.symbols),
        dry_run=settings.dry_run,
        min_edge_bps=settings.min_edge_bps,
    )

    exchanges = {cfg.exchange_id: build_exchange(cfg) for cfg in settings.exchanges}
    for ex in exchanges.values():
        await ex.load_markets()

    store = BookStore()
    fees = {cfg.exchange_id: cfg.taker_fee for cfg in settings.exchanges}
    scanner = SpreadScanner(settings, fees)
    executor = Executor(settings, exchanges)
    rebalancer = Rebalancer(settings, exchanges, executor, executor.journal)
    health = HealthServer(store, settings.health_port)

    await executor.reconcile_on_start()

    tasks = [
        asyncio.create_task(
            supervise(
                f"feed:{ex_id}:{symbol}",
                lambda ex=exchanges[ex_id], s=symbol: orderbook_feed(ex, s, store, settings),
                settings,
            ),
            name=f"feed:{ex_id}:{symbol}",
        )
        for ex_id in exchanges
        for symbol in settings.symbols
    ]
    tasks.append(
        asyncio.create_task(
            supervise(
                "decision_loop",
                lambda: decision_loop(settings, store, scanner, executor, health),
                settings,
            ),
            name="decision_loop",
        )
    )
    if not settings.dry_run or settings.enable_withdrawals:
        tasks.append(
            asyncio.create_task(
                supervise("rebalancer", rebalancer.run_forever, settings),
                name="rebalancer",
            )
        )
    tasks.append(
        asyncio.create_task(
            supervise("health", health.run_forever, settings), name="health"
        )
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log_event(logger, logging.INFO, "shutdown_begin")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for ex in exchanges.values():
        try:
            await ex.close()
        except Exception:  # noqa: BLE001
            pass
    log_event(logger, logging.INFO, "shutdown_complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
