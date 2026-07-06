"""Execution manager: dual-leg IOC execution with write-ahead journaling.

Both legs are submitted concurrently as immediate-or-cancel limit orders at
the worst acceptable book level, so a fill can never be worse than the price
the engine already deemed profitable. Partial-fill asymmetry between legs is
reconciled immediately with a market order that flattens the residual.

In DRY_RUN mode no order leaves the process: fills are simulated at the
engine's VWAP and journaled identically, so paper and live runs produce the
same audit trail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import ccxt.pro as ccxtpro

from .config import Settings
from .logging_setup import log_event
from .models import LegResult, Opportunity, TradeRecord

logger = logging.getLogger("executor")


class Journal:
    """Append-only NDJSON write-ahead journal for crash reconciliation."""

    def __init__(self, state_dir: str) -> None:
        os.makedirs(state_dir, exist_ok=True)
        self.path = os.path.join(state_dir, "journal.ndjson")

    def append(self, kind: str, payload: dict) -> None:
        entry = {"ts": round(time.time(), 3), "kind": kind, **payload}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


class Executor:
    def __init__(
        self,
        settings: Settings,
        exchanges: dict[str, ccxtpro.Exchange],
    ) -> None:
        self.settings = settings
        self.exchanges = exchanges
        self.journal = Journal(settings.state_dir)
        self._last_trade_ts = 0.0
        self.cumulative_pnl_quote = 0.0
        self.trade_count = 0

    # ------------------------------------------------------------------ #
    # startup reconciliation                                             #
    # ------------------------------------------------------------------ #

    async def reconcile_on_start(self) -> None:
        """Cancel any orders left open by a previous crashed run."""
        if self.settings.dry_run:
            return
        for ex_id, ex in self.exchanges.items():
            for symbol in self.settings.symbols:
                try:
                    open_orders = await ex.fetch_open_orders(symbol)
                    for order in open_orders:
                        await ex.cancel_order(order["id"], symbol)
                        log_event(
                            logger, logging.WARNING, "stale_order_cancelled",
                            exchange=ex_id, symbol=symbol, order_id=order["id"],
                        )
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        logger, logging.ERROR, "reconcile_error",
                        exchange=ex_id, symbol=symbol, error=str(exc)[:300],
                    )

    # ------------------------------------------------------------------ #
    # trade execution                                                    #
    # ------------------------------------------------------------------ #

    def in_cooldown(self) -> bool:
        return (time.monotonic() - self._last_trade_ts) < self.settings.trade_cooldown_s

    async def execute(self, opp: Opportunity) -> TradeRecord | None:
        if self.in_cooldown():
            return None
        self._last_trade_ts = time.monotonic()

        trade_id = uuid.uuid4().hex[:12]
        self.journal.append(
            "intent",
            {
                "trade_id": trade_id,
                "symbol": opp.symbol,
                "buy_exchange": opp.buy_exchange,
                "sell_exchange": opp.sell_exchange,
                "qty": opp.quantity,
                "buy_limit": opp.buy_limit_price,
                "sell_limit": opp.sell_limit_price,
                "expected_edge_bps": opp.edge_bps,
            },
        )

        if self.settings.dry_run:
            buy_leg, sell_leg = self._simulate_legs(opp)
        else:
            buy_leg, sell_leg = await self._submit_legs(opp)
            buy_leg, sell_leg = await self._flatten_residual(opp, buy_leg, sell_leg)

        pnl = self._realized_pnl(buy_leg, sell_leg)
        self.cumulative_pnl_quote += pnl
        self.trade_count += 1
        record = TradeRecord(
            opportunity=opp,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            realized_pnl_quote=pnl,
            completed_at_ms=time.time() * 1000.0,
        )
        self.journal.append(
            "result",
            {
                "trade_id": trade_id,
                "pnl_quote": round(pnl, 6),
                "buy_filled": buy_leg.filled_qty if buy_leg else 0.0,
                "sell_filled": sell_leg.filled_qty if sell_leg else 0.0,
                "cumulative_pnl": round(self.cumulative_pnl_quote, 6),
                "dry_run": self.settings.dry_run,
            },
        )
        log_event(
            logger, logging.INFO, "trade_complete",
            trade_id=trade_id, pnl_quote=round(pnl, 6),
            cumulative_pnl=round(self.cumulative_pnl_quote, 6),
            trades=self.trade_count, dry_run=self.settings.dry_run,
        )
        return record

    def _simulate_legs(self, opp: Opportunity) -> tuple[LegResult, LegResult]:
        buy_fee = opp.quantity * opp.buy_vwap * self._fee(opp.buy_exchange)
        sell_fee = opp.quantity * opp.sell_vwap * self._fee(opp.sell_exchange)
        buy = LegResult(
            exchange_id=opp.buy_exchange, side="buy",
            requested_qty=opp.quantity, filled_qty=opp.quantity,
            avg_price=opp.buy_vwap, fee_quote=buy_fee,
            order_id=f"sim-{uuid.uuid4().hex[:8]}",
        )
        sell = LegResult(
            exchange_id=opp.sell_exchange, side="sell",
            requested_qty=opp.quantity, filled_qty=opp.quantity,
            avg_price=opp.sell_vwap, fee_quote=sell_fee,
            order_id=f"sim-{uuid.uuid4().hex[:8]}",
        )
        return buy, sell

    def _fee(self, exchange_id: str) -> float:
        for cfg in self.settings.exchanges:
            if cfg.exchange_id == exchange_id:
                return cfg.taker_fee
        return 0.001

    async def _submit_legs(self, opp: Opportunity) -> tuple[LegResult | None, LegResult | None]:
        buy_task = self._place_ioc(
            opp.buy_exchange, opp.symbol, "buy", opp.quantity, opp.buy_limit_price
        )
        sell_task = self._place_ioc(
            opp.sell_exchange, opp.symbol, "sell", opp.quantity, opp.sell_limit_price
        )
        buy_leg, sell_leg = await asyncio.gather(buy_task, sell_task)
        return buy_leg, sell_leg

    async def _place_ioc(
        self, exchange_id: str, symbol: str, side: str, qty: float, limit_price: float
    ) -> LegResult | None:
        ex = self.exchanges[exchange_id]
        try:
            amount = float(ex.amount_to_precision(symbol, qty))
            price = float(ex.price_to_precision(symbol, limit_price))
            order = await ex.create_order(
                symbol, "limit", side, amount, price, {"timeInForce": "IOC"}
            )
            # IOC settles immediately; fetch the final state for fill data.
            final = await ex.fetch_order(order["id"], symbol)
            filled = float(final.get("filled") or 0.0)
            avg = float(final.get("average") or price)
            fee_quote = 0.0
            for fee in final.get("fees") or []:
                if fee.get("cost"):
                    fee_quote += float(fee["cost"])
            return LegResult(
                exchange_id=exchange_id, side=side,
                requested_qty=amount, filled_qty=filled,
                avg_price=avg, fee_quote=fee_quote, order_id=str(order["id"]),
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, logging.ERROR, "leg_failed",
                exchange=exchange_id, symbol=symbol, side=side,
                error=type(exc).__name__, detail=str(exc)[:300],
            )
            return None

    async def _flatten_residual(
        self,
        opp: Opportunity,
        buy_leg: LegResult | None,
        sell_leg: LegResult | None,
    ) -> tuple[LegResult | None, LegResult | None]:
        """If leg fills are asymmetric, flatten the excess with a market order
        on the venue that over-filled, eliminating directional exposure."""
        bought = buy_leg.filled_qty if buy_leg else 0.0
        sold = sell_leg.filled_qty if sell_leg else 0.0
        residual = bought - sold
        if abs(residual) < 1e-10:
            return buy_leg, sell_leg
        if residual > 0:
            ex_id, side = opp.buy_exchange, "sell"
        else:
            ex_id, side = opp.sell_exchange, "buy"
        ex = self.exchanges[ex_id]
        try:
            amount = float(ex.amount_to_precision(opp.symbol, abs(residual)))
            if amount > 0:
                await ex.create_order(opp.symbol, "market", side, amount)
                log_event(
                    logger, logging.WARNING, "residual_flattened",
                    exchange=ex_id, symbol=opp.symbol, side=side, qty=amount,
                )
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, logging.CRITICAL, "residual_flatten_failed",
                exchange=ex_id, symbol=opp.symbol, qty=residual,
                error=str(exc)[:300],
            )
        return buy_leg, sell_leg

    @staticmethod
    def _realized_pnl(buy: LegResult | None, sell: LegResult | None) -> float:
        proceeds = (sell.filled_qty * sell.avg_price - sell.fee_quote) if sell else 0.0
        cost = (buy.filled_qty * buy.avg_price + buy.fee_quote) if buy else 0.0
        return proceeds - cost
