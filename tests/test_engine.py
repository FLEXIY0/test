"""Unit tests for the spread-detection math (no network, no ccxt needed)."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arbitrage.config import load_settings
from arbitrage.engine import optimal_quantity, price_opportunity, vwap_for_quantity
from arbitrage.models import BookSnapshot


def make_settings(**overrides):
    defaults = {
        "MIN_EDGE_BPS": "5",
        "MAX_NOTIONAL_PER_TRADE": "100000",
        "BOOK_MAX_AGE_MS": "750",
        "EXCHANGES": "",
    }
    defaults.update(overrides)
    saved = {k: os.environ.get(k) for k in defaults}
    os.environ.update(defaults)
    try:
        return load_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def snap(exchange_id, bids, asks):
    return BookSnapshot(
        exchange_id=exchange_id, symbol="BTC/USDT",
        bids=bids, asks=asks, ts_ms=time.time() * 1000.0,
    )


class TestVwap:
    def test_single_level(self):
        vwap, worst = vwap_for_quantity([(100.0, 5.0)], 2.0)
        assert vwap == 100.0
        assert worst == 100.0

    def test_multi_level_weighting(self):
        # 1 @ 100 + 1 @ 102 -> vwap 101, worst 102
        vwap, worst = vwap_for_quantity([(100.0, 1.0), (102.0, 3.0)], 2.0)
        assert vwap == pytest.approx(101.0)
        assert worst == 102.0

    def test_insufficient_depth_raises(self):
        with pytest.raises(ValueError):
            vwap_for_quantity([(100.0, 1.0)], 2.0)


class TestOptimalQuantity:
    def test_no_crossing_gives_zero(self):
        # bid below ask: nothing profitable
        qty = optimal_quantity(
            asks=[(100.0, 5.0)], bids=[(99.0, 5.0)],
            buy_fee=0.001, sell_fee=0.001, min_edge_bps=5,
        )
        assert qty == 0.0

    def test_fees_kill_a_thin_spread(self):
        # 5 bps raw spread, 10 bps of fees each side -> not viable
        qty = optimal_quantity(
            asks=[(100.00, 5.0)], bids=[(100.05, 5.0)],
            buy_fee=0.001, sell_fee=0.001, min_edge_bps=5,
        )
        assert qty == 0.0

    def test_walk_stops_at_unprofitable_level(self):
        # First ask level profitable vs bids, second not.
        qty = optimal_quantity(
            asks=[(100.0, 1.0), (101.0, 10.0)],
            bids=[(100.9, 5.0)],
            buy_fee=0.0, sell_fee=0.0, min_edge_bps=5,
        )
        assert qty == pytest.approx(1.0)

    def test_quantity_limited_by_thinner_side(self):
        qty = optimal_quantity(
            asks=[(100.0, 10.0)],
            bids=[(101.0, 2.0)],
            buy_fee=0.0, sell_fee=0.0, min_edge_bps=5,
        )
        assert qty == pytest.approx(2.0)


class TestPriceOpportunity:
    def test_full_pricing_after_fees(self):
        settings = make_settings()
        buy_book = snap("ex_a", bids=[(99.0, 10.0)], asks=[(100.0, 10.0)])
        sell_book = snap("ex_b", bids=[(101.0, 10.0)], asks=[(102.0, 10.0)])
        opp = price_opportunity(
            "BTC/USDT", buy_book, sell_book,
            buy_fee=0.001, sell_fee=0.001,
            transfer_cost_quote=0.0, settings=settings,
        )
        assert opp is not None
        assert opp.quantity == pytest.approx(10.0)
        # net = 10*101*0.999 - 10*100*1.001 = 1008.99 - 1001.0 = 7.99
        assert opp.net_edge_quote == pytest.approx(7.99, abs=1e-6)
        assert opp.edge_bps == pytest.approx(10_000 * 7.99 / 1000.0, rel=1e-6)

    def test_notional_cap_clamps_quantity(self):
        settings = make_settings(MAX_NOTIONAL_PER_TRADE="100")
        buy_book = snap("ex_a", bids=[(99.0, 10.0)], asks=[(100.0, 10.0)])
        sell_book = snap("ex_b", bids=[(101.0, 10.0)], asks=[(102.0, 10.0)])
        opp = price_opportunity(
            "BTC/USDT", buy_book, sell_book,
            buy_fee=0.001, sell_fee=0.001,
            transfer_cost_quote=0.0, settings=settings,
        )
        assert opp is not None
        assert opp.quantity == pytest.approx(1.0)  # 100 quote / 100 price

    def test_transfer_cost_can_kill_edge(self):
        settings = make_settings()
        buy_book = snap("ex_a", bids=[(99.0, 10.0)], asks=[(100.0, 10.0)])
        sell_book = snap("ex_b", bids=[(101.0, 10.0)], asks=[(102.0, 10.0)])
        opp = price_opportunity(
            "BTC/USDT", buy_book, sell_book,
            buy_fee=0.001, sell_fee=0.001,
            transfer_cost_quote=50.0, settings=settings,  # eats the 7.99 edge
        )
        assert opp is None

    def test_empty_book_returns_none(self):
        settings = make_settings()
        buy_book = snap("ex_a", bids=[], asks=[])
        sell_book = snap("ex_b", bids=[(101.0, 10.0)], asks=[(102.0, 10.0)])
        assert price_opportunity(
            "BTC/USDT", buy_book, sell_book, 0.001, 0.001, 0.0, settings
        ) is None
