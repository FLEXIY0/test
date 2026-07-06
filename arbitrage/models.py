"""Core domain objects shared across feed, engine and executor."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class BookSnapshot:
    """Immutable-by-convention snapshot of one venue's order book."""

    exchange_id: str
    symbol: str
    bids: list[tuple[float, float]]   # [(price, size)] descending price
    asks: list[tuple[float, float]]   # [(price, size)] ascending price
    ts_ms: float                      # local receive timestamp

    def age_ms(self) -> float:
        return time.time() * 1000.0 - self.ts_ms

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else float("inf")


@dataclass
class Opportunity:
    """A fully-priced executable cross-exchange opportunity."""

    symbol: str
    buy_exchange: str
    sell_exchange: str
    quantity: float               # base units to trade on each leg
    buy_vwap: float               # expected average fill on the buy leg
    sell_vwap: float              # expected average fill on the sell leg
    buy_limit_price: float        # worst acceptable price (deepest level used)
    sell_limit_price: float
    gross_edge_quote: float       # before fees
    net_edge_quote: float         # after fees + amortised transfer cost
    edge_bps: float
    detected_at_ms: float = field(default_factory=lambda: time.time() * 1000.0)


@dataclass
class LegResult:
    """Outcome of one order leg after settlement."""

    exchange_id: str
    side: str                     # "buy" | "sell"
    requested_qty: float
    filled_qty: float
    avg_price: float
    fee_quote: float
    order_id: str


@dataclass
class TradeRecord:
    """Journal entry for one two-legged arbitrage execution."""

    opportunity: Opportunity
    buy_leg: LegResult | None
    sell_leg: LegResult | None
    realized_pnl_quote: float
    completed_at_ms: float
