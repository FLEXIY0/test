"""Inventory rebalancer and profit sweeper.

Spatial arbitrage in inventory mode systematically drains the base asset on
the sell-heavy venue and quote on the buy-heavy venue. This task periodically
measures the skew and, past the configured threshold, moves assets between
venues via the exchanges' official withdrawal APIs. Profit above the working
capital watermark is swept to a non-custodial wallet address.

Both actions require ENABLE_WITHDRAWALS=true; with it off (default) the task
only logs skew so operators can rebalance out-of-band. Withdrawal API keys
should be scoped separately (see docs/SECURITY.md).
"""

from __future__ import annotations

import asyncio
import logging
import time

import ccxt.pro as ccxtpro

from .config import Settings
from .executor import Executor, Journal
from .logging_setup import log_event

logger = logging.getLogger("rebalancer")


def split_symbol(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/")
    return base, quote


class Rebalancer:
    def __init__(
        self,
        settings: Settings,
        exchanges: dict[str, ccxtpro.Exchange],
        executor: Executor,
        journal: Journal,
    ) -> None:
        self.settings = settings
        self.exchanges = exchanges
        self.executor = executor
        self.journal = journal
        self.initial_capital_quote: float | None = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger, logging.ERROR, "rebalance_cycle_error",
                    error=type(exc).__name__, detail=str(exc)[:300],
                )
            await asyncio.sleep(self.settings.rebalance_interval_s)

    async def _cycle(self) -> None:
        balances = await self._fetch_balances()
        if not balances:
            return
        for symbol in self.settings.symbols:
            base, quote = split_symbol(symbol)
            await self._rebalance_asset(base, balances)
        await self._sweep_profit(balances)

    async def _fetch_balances(self) -> dict[str, dict[str, float]]:
        """exchange_id -> {currency: free amount}."""
        out: dict[str, dict[str, float]] = {}
        for ex_id, ex in self.exchanges.items():
            try:
                raw = await ex.fetch_balance()
                out[ex_id] = {
                    cur: float(amt)
                    for cur, amt in (raw.get("free") or {}).items()
                    if isinstance(amt, (int, float)) and float(amt) > 0
                }
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger, logging.WARNING, "balance_fetch_failed",
                    exchange=ex_id, error=str(exc)[:300],
                )
        return out

    async def _rebalance_asset(
        self, currency: str, balances: dict[str, dict[str, float]]
    ) -> None:
        holdings = {ex: bal.get(currency, 0.0) for ex, bal in balances.items()}
        total = sum(holdings.values())
        if total <= 0 or len(holdings) < 2:
            return
        rich = max(holdings, key=holdings.get)
        poor = min(holdings, key=holdings.get)
        skew = (holdings[rich] - holdings[poor]) / total
        log_event(
            logger, logging.INFO, "inventory_skew",
            currency=currency, skew=round(skew, 4),
            holdings={k: round(v, 8) for k, v in holdings.items()},
        )
        if skew < self.settings.rebalance_skew_threshold:
            return
        amount = (holdings[rich] - holdings[poor]) / 2.0
        if not self.settings.enable_withdrawals or self.settings.dry_run:
            log_event(
                logger, logging.WARNING, "rebalance_needed_but_withdrawals_disabled",
                currency=currency, from_exchange=rich, to_exchange=poor,
                amount=round(amount, 8),
            )
            return
        await self._transfer(currency, rich, poor, amount)

    async def _transfer(
        self, currency: str, from_ex: str, to_ex: str, amount: float
    ) -> None:
        src = self.exchanges[from_ex]
        dst = self.exchanges[to_ex]
        try:
            deposit = await dst.fetch_deposit_address(currency)
            address = deposit["address"]
            tag = deposit.get("tag")
            self.journal.append(
                "rebalance_intent",
                {"currency": currency, "from": from_ex, "to": to_ex, "amount": amount},
            )
            result = await src.withdraw(
                currency, amount, address, tag, {"network": deposit.get("network")}
            )
            # Feed the realised withdrawal fee back into the engine's
            # amortised transfer cost estimate (ARCHITECTURE.md §2.4).
            fee = float((result.get("fee") or {}).get("cost") or 0.0)
            log_event(
                logger, logging.INFO, "rebalance_withdrawal_sent",
                currency=currency, from_exchange=from_ex, to_exchange=to_ex,
                amount=round(amount, 8), fee=fee, tx_id=result.get("id"),
            )
        except Exception as exc:  # noqa: BLE001
            log_event(
                logger, logging.ERROR, "rebalance_transfer_failed",
                currency=currency, from_exchange=from_ex, to_exchange=to_ex,
                error=type(exc).__name__, detail=str(exc)[:300],
            )

    async def _sweep_profit(self, balances: dict[str, dict[str, float]]) -> None:
        """Sweep quote-currency profit above the initial-capital watermark to
        the configured non-custodial wallet."""
        if not self.settings.profit_sweep_address:
            return
        quote_currencies = {split_symbol(s)[1] for s in self.settings.symbols}
        for quote in quote_currencies:
            total_quote = sum(bal.get(quote, 0.0) for bal in balances.values())
            if self.initial_capital_quote is None:
                self.initial_capital_quote = total_quote
                log_event(
                    logger, logging.INFO, "capital_watermark_set",
                    quote=quote, watermark=round(total_quote, 2),
                )
                continue
            surplus = total_quote - self.initial_capital_quote
            if surplus < self.settings.profit_sweep_min_quote:
                continue
            if not self.settings.enable_withdrawals or self.settings.dry_run:
                log_event(
                    logger, logging.INFO, "profit_sweep_deferred",
                    quote=quote, surplus=round(surplus, 2),
                    reason="withdrawals disabled or dry run",
                )
                continue
            richest = max(balances, key=lambda ex: balances[ex].get(quote, 0.0))
            amount = min(surplus, balances[richest].get(quote, 0.0))
            try:
                result = await self.exchanges[richest].withdraw(
                    quote, amount, self.settings.profit_sweep_address, None,
                    {"network": self.settings.profit_sweep_network or None},
                )
                self.journal.append(
                    "profit_sweep",
                    {"quote": quote, "amount": amount, "tx_id": result.get("id")},
                )
                log_event(
                    logger, logging.INFO, "profit_swept",
                    quote=quote, amount=round(amount, 2),
                    address=self.settings.profit_sweep_address[:10] + "…",
                )
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger, logging.ERROR, "profit_sweep_failed",
                    quote=quote, error=str(exc)[:300],
                )
