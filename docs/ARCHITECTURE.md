# Architectural Blueprint — Cross-Exchange Arbitrage Daemon

## 1. System Topology

```
┌────────────────────────────────────────────────────────────────────┐
│                        arbitrage daemon (asyncio)                  │
│                                                                    │
│  ┌──────────────┐   ┌──────────────┐        ┌──────────────┐       │
│  │ Feed: Ex. A  │   │ Feed: Ex. B  │  ...   │ Feed: Ex. N  │       │
│  │ (WS orderbook│   │ (WS orderbook│        │ (WS orderbook│       │
│  │  + reconnect)│   │  + reconnect)│        │  + reconnect)│       │
│  └──────┬───────┘   └──────┬───────┘        └──────┬───────┘       │
│         └────────────┬─────┴───────────────────────┘               │
│                      ▼                                             │
│           ┌─────────────────────┐                                  │
│           │  Shared book store  │  (in-memory, timestamped)        │
│           └─────────┬───────────┘                                  │
│                     ▼                                              │
│           ┌─────────────────────┐     ┌──────────────────────┐     │
│           │   Decision engine   │────▶│  Execution manager   │     │
│           │  (VWAP spread calc, │     │ (dual-leg IOC orders,│     │
│           │   fee-adjusted edge)│     │  dry-run / live)     │     │
│           └─────────────────────┘     └─────────┬────────────┘     │
│                     ▲                           ▼                  │
│           ┌─────────┴───────────┐     ┌──────────────────────┐     │
│           │ Health-check server │     │ Inventory rebalancer │     │
│           │  (HTTP :8080)       │     │ (skew monitor)       │     │
│           └─────────────────────┘     └──────────────────────┘     │
└────────────────────────────────────────────────────────────────────┘
```

Data flow (constraint 6): **Feed X (Exchange A order book) → Decision Engine Y
(fee-adjusted VWAP spread model) → Execution Vector Z (simultaneous buy on A /
sell on B)**. All components run as supervised asyncio tasks inside one
process; the process itself is supervised by Docker (`restart: unless-stopped`),
making the daemon self-recovering end-to-end.

## 2. Mathematical Model

### 2.1 Executable price, not top-of-book price

Top-of-book spread is a mirage: the visible best bid/ask usually carries too
little size. The engine therefore prices every candidate trade against the
**full depth** of both books using the volume-weighted average fill price.

For a buy of quantity `q` (base units) against ask levels
`(p₁,s₁), (p₂,s₂), …` sorted ascending:

```
VWAP_buy(q) = ( Σᵢ pᵢ · min(sᵢ, q − Σⱼ<ᵢ sⱼ) ) / q
```

Symmetrically `VWAP_sell(q)` against bids sorted descending.

### 2.2 Fee-adjusted net edge

Let `f_A`, `f_B` be taker fee rates on the buy and sell venue, `q` the traded
base quantity, and `C_x` the amortised transfer/rebalancing cost per trade
(quote units). The **net edge** in quote currency:

```
E(q) = q · VWAP_sell(q) · (1 − f_B)  −  q · VWAP_buy(q) · (1 + f_A)  −  C_x
```

Expressed in basis points of deployed notional:

```
edge_bps(q) = 10⁴ · E(q) / ( q · VWAP_buy(q) )
```

A trade fires only if `edge_bps(q) ≥ θ`, where `θ = MIN_EDGE_BPS` (config).

### 2.3 Optimal size via marginal-price matching

`E(q)` is concave piecewise-linear in `q`, so the maximiser is found by a
single synchronized walk down both books: keep consuming liquidity while the
**marginal** unit is still profitable:

```
take while:   bid_price · (1 − f_B)  >  ask_price · (1 + f_A) · (1 + θ·10⁻⁴)
```

The walk stops at the first level pair violating the inequality; the
accumulated quantity `q*` is the profit-maximising size. `q*` is then clamped
by (a) available inventory on each leg, (b) `MAX_NOTIONAL_PER_TRADE`, and
(c) exchange minimum order size — all before execution.

### 2.4 Transfer / rebalancing cost `C_x`

Spatial arbitrage drains base asset on venue B and quote on venue A. Two
regimes:

- **Inventory mode (default, market-neutral):** hold both assets on both
  venues; trades only shift inventory, no on-chain transfer per trade. `C_x`
  amortises the periodic rebalancing withdrawal:
  `C_x = W_fee · (q · p̄) / V_rebalance`, where `W_fee` is the flat withdrawal
  fee and `V_rebalance` the notional moved per rebalance cycle.
- **Transfer mode:** per-trade withdrawal — dominated by network fee and
  latency risk; only viable when `E(q) ≫ W_fee`. Disabled by default
  (`ENABLE_WITHDRAWALS=false`).

Because each opportunity is executed as a *simultaneous* buy+sell of equal
size, net directional exposure per trade is ≈ 0 (constraint 8); residual risk
is leg-fill asymmetry, bounded by using IOC (immediate-or-cancel) orders and
reconciling partial fills.

### 2.5 Triangular arbitrage (single venue)

For a cycle `Q → B1 → B2 → Q` with pairs `B1/Q`, `B2/B1`, `B2/Q` and taker fee
`f`, the cycle multiplier from one quote unit is:

```
M = (1−f)³ · (1 / ask(B1/Q)) · (1 / ask(B2/B1)) · bid(B2/Q)
```

Fire when `M > 1 + θ·10⁻⁴`, sizing each leg by the depth-constrained VWAP of
the same walk as §2.3. The engine evaluates both cycle directions
(`Q→B1→B2→Q` and `Q→B2→B1→Q`).

### 2.6 Staleness & race protection

An opportunity computed from books observed at times `t_A`, `t_B` is discarded
if `max(now−t_A, now−t_B) > BOOK_MAX_AGE_MS` (default 750 ms). This bounds
the probability of acting on a spread that has already been consumed.

## 3. Resilience Model (constraint 1, 7)

- Every WebSocket feed runs in its own supervised task; on any exception the
  task reconnects with **exponential back-off + jitter**
  (`min(base·2ⁿ, cap) · U(0.5, 1.0)`), resetting `n` after a stable minute.
- All REST calls go through ccxt's built-in rate limiter
  (`enableRateLimit=true`) plus the same back-off wrapper on `429`/`5xx`.
- Structured JSON logging (one event per line) → stdout → Docker log driver.
- `/healthz` HTTP endpoint reports per-feed staleness; Docker `HEALTHCHECK`
  restarts the container if any feed is dead > 60 s.
- Execution states are persisted to a journal file (`state/journal.ndjson`)
  before order submission (write-ahead), so a crash mid-trade is reconciled on
  restart by querying open orders and cancelling stragglers.

## 4. Capital Efficiency (constraint 8)

- Inventory mode keeps capital on-exchange, turnover limited only by
  opportunity frequency — zero blockchain latency in the hot path.
- Per-trade exposure window = max(order round-trip on A, on B) ≈ tens of ms
  on co-located VPS; both legs use IOC so no resting inventory risk.
- The rebalancer monitors skew `|inv_A − inv_B| / (inv_A + inv_B)` and acts
  only past `REBALANCE_SKEW_THRESHOLD`, batching transfers to amortise fees.
