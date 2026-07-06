# Cross-Exchange Arbitrage Daemon

Autonomous, market-neutral spatial-arbitrage daemon: streams order books from
multiple exchanges over official WebSocket APIs (ccxt.pro), prices
fee-adjusted VWAP spreads across the full book depth, and executes
simultaneous IOC buy/sell legs when the net edge clears a configurable
threshold. Designed for unattended 24/7 operation under Docker.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system topology and the
  mathematical model (VWAP fill pricing, marginal-price sizing, fee/transfer
  cost accounting, staleness gating).
- [`docs/SECURITY.md`](docs/SECURITY.md) — API-key policy, Docker secrets,
  container and host hardening.

## Layout

```
arbitrage/
  config.py         env-driven settings + Docker-secret reader
  models.py         BookSnapshot / Opportunity / LegResult / TradeRecord
  feeds.py          supervised WS order-book feeds, exponential back-off
  engine.py         spread scanner: optimal size + fee-adjusted net edge
  executor.py       dual-leg IOC execution, WAL journal, residual flattening
  rebalancer.py     inventory skew monitor, transfers, profit sweep
  health.py         /healthz endpoint for Docker HEALTHCHECK
  main.py           task supervisor / entrypoint
tests/              engine math unit tests
Dockerfile          multi-stage, non-root, read-only-friendly
docker-compose.yml  restart policy, secrets, hardening, log rotation
.env.example        all runtime knobs (non-secret)
```

## Quick start (paper trading — the default)

```bash
cp .env.example .env                  # adjust venues/symbols/fees
mkdir -p secrets && umask 077
printf '%s' 'KEY'    > secrets/binance_api_key
printf '%s' 'SECRET' > secrets/binance_api_secret
printf '%s' 'KEY'    > secrets/kraken_api_key
printf '%s' 'SECRET' > secrets/kraken_api_secret

docker compose up -d --build
curl -s localhost:8080/healthz | python3 -m json.tool   # feed freshness + PnL
docker compose logs -f                                   # structured JSON events
```

`DRY_RUN=true` runs the entire pipeline against live market data with
simulated fills and a real journal (`state/journal.ndjson`). Review paper
PnL over a meaningful window before setting `DRY_RUN=false`.

Public order books stream without credentials, so paper mode works with
empty secret files; live trading requires trade-scoped keys
(see `docs/SECURITY.md`).

## Going live — deliberate steps

1. Verify your actual fee tier per venue and set `*_TAKER_FEE` accordingly —
   the edge model is only as good as its fee inputs.
2. Fund both venues with base *and* quote inventory (inventory mode needs
   both sides pre-positioned).
3. Set `DRY_RUN=false`, keep `ENABLE_WITHDRAWALS=false`, restart, observe.
4. Only then consider `ENABLE_WITHDRAWALS=true` with a withdrawal-scoped,
   address-allowlisted key for automated rebalancing and profit sweeps.

## Tests

```bash
python -m pytest tests/ -q
```

## Reality check

The code is a complete, correct implementation of the strategy, but spatial
arbitrage is a latency- and fee-sensitive business: realized edge depends on
your fee tier, VPS placement relative to exchange gateways, and competition
from faster participants. `MIN_EDGE_BPS`, fees, and paper-trading results —
not hope — should drive the go-live decision. Nothing here is investment
advice; comply with the exchanges' terms of service and your local
regulations.
