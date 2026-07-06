"""Decision engine: fee-adjusted cross-exchange spread detection.

Implements the model from docs/ARCHITECTURE.md §2:

* marginal-price walk over both books simultaneously to find the
  profit-maximising quantity q* (§2.3);
* VWAP pricing of the resulting fill (§2.1);
* net edge after taker fees and amortised transfer cost (§2.2);
* staleness gating (§2.6).
"""

from __future__ import annotations

import itertools
import logging

from .config import Settings
from .logging_setup import log_event
from .models import BookSnapshot, Opportunity

logger = logging.getLogger("engine")


def vwap_for_quantity(levels: list[tuple[float, float]], qty: float) -> tuple[float, float]:
    """Average fill price and worst level price for taking `qty` from `levels`.

    Returns (vwap, worst_price). Raises ValueError if depth is insufficient.
    """
    remaining = qty
    cost = 0.0
    worst = 0.0
    for price, size in levels:
        take = min(size, remaining)
        cost += take * price
        worst = price
        remaining -= take
        if remaining <= 1e-12:
            return cost / qty, worst
    raise ValueError(f"insufficient depth for qty={qty}")


def optimal_quantity(
    asks: list[tuple[float, float]],
    bids: list[tuple[float, float]],
    buy_fee: float,
    sell_fee: float,
    min_edge_bps: float,
) -> float:
    """Marginal-price walk: max quantity for which every marginal unit clears
    the fee-adjusted threshold  bid·(1−f_s) > ask·(1+f_b)·(1+θ)."""
    threshold = 1.0 + min_edge_bps / 10_000.0
    qty = 0.0
    ai, bi = 0, 0
    ask_left = asks[0][1] if asks else 0.0
    bid_left = bids[0][1] if bids else 0.0
    while ai < len(asks) and bi < len(bids):
        ask_price = asks[ai][0]
        bid_price = bids[bi][0]
        if bid_price * (1.0 - sell_fee) <= ask_price * (1.0 + buy_fee) * threshold:
            break
        take = min(ask_left, bid_left)
        qty += take
        ask_left -= take
        bid_left -= take
        if ask_left <= 1e-12:
            ai += 1
            ask_left = asks[ai][1] if ai < len(asks) else 0.0
        if bid_left <= 1e-12:
            bi += 1
            bid_left = bids[bi][1] if bi < len(bids) else 0.0
    return qty


def price_opportunity(
    symbol: str,
    buy_book: BookSnapshot,
    sell_book: BookSnapshot,
    buy_fee: float,
    sell_fee: float,
    transfer_cost_quote: float,
    settings: Settings,
) -> Opportunity | None:
    """Fully price a candidate buy-on-A / sell-on-B trade; None if not viable."""
    if not buy_book.asks or not sell_book.bids:
        return None

    qty = optimal_quantity(
        buy_book.asks, sell_book.bids, buy_fee, sell_fee, settings.min_edge_bps
    )
    if qty <= 0.0:
        return None

    # Clamp by the notional cap using the best ask as the price proxy,
    # then re-price at the clamped size.
    max_qty_by_notional = settings.max_notional_per_trade / buy_book.best_ask
    qty = min(qty, max_qty_by_notional)
    if qty <= 0.0:
        return None

    try:
        buy_vwap, buy_worst = vwap_for_quantity(buy_book.asks, qty)
        sell_vwap, sell_worst = vwap_for_quantity(sell_book.bids, qty)
    except ValueError:
        return None

    gross = qty * (sell_vwap - buy_vwap)
    net = (
        qty * sell_vwap * (1.0 - sell_fee)
        - qty * buy_vwap * (1.0 + buy_fee)
        - transfer_cost_quote
    )
    notional = qty * buy_vwap
    if notional <= 0.0:
        return None
    edge_bps = 10_000.0 * net / notional
    if edge_bps < settings.min_edge_bps:
        return None

    return Opportunity(
        symbol=symbol,
        buy_exchange=buy_book.exchange_id,
        sell_exchange=sell_book.exchange_id,
        quantity=qty,
        buy_vwap=buy_vwap,
        sell_vwap=sell_vwap,
        buy_limit_price=buy_worst,
        sell_limit_price=sell_worst,
        gross_edge_quote=gross,
        net_edge_quote=net,
        edge_bps=edge_bps,
    )


class SpreadScanner:
    """Scans every ordered pair of venues for every configured symbol."""

    def __init__(self, settings: Settings, fees: dict[str, float]) -> None:
        self.settings = settings
        self.taker_fees = fees                      # exchange_id -> taker fee
        # Amortised transfer cost per trade (quote units); refined at runtime
        # by the rebalancer from observed withdrawal fees. 0 in inventory mode
        # until the first rebalance measures the real cost.
        self.transfer_cost_quote: float = 0.0

    def scan(
        self, books: dict[tuple[str, str], BookSnapshot | None]
    ) -> list[Opportunity]:
        found: list[Opportunity] = []
        exchange_ids = sorted({ex for ex, _ in books if books[(ex, _)] is not None})
        for symbol in self.settings.symbols:
            fresh: dict[str, BookSnapshot] = {}
            for ex_id in exchange_ids:
                snap = books.get((ex_id, symbol))
                if snap is not None and snap.age_ms() <= self.settings.book_max_age_ms:
                    fresh[ex_id] = snap
            for buy_ex, sell_ex in itertools.permutations(fresh, 2):
                opp = price_opportunity(
                    symbol=symbol,
                    buy_book=fresh[buy_ex],
                    sell_book=fresh[sell_ex],
                    buy_fee=self.taker_fees[buy_ex],
                    sell_fee=self.taker_fees[sell_ex],
                    transfer_cost_quote=self.transfer_cost_quote,
                    settings=self.settings,
                )
                if opp is not None:
                    log_event(
                        logger, logging.INFO, "opportunity",
                        symbol=symbol, buy=buy_ex, sell=sell_ex,
                        qty=round(opp.quantity, 8),
                        edge_bps=round(opp.edge_bps, 2),
                        net_quote=round(opp.net_edge_quote, 4),
                    )
                    found.append(opp)
        # Best edge first; the executor takes one per cooldown window.
        found.sort(key=lambda o: o.net_edge_quote, reverse=True)
        return found
