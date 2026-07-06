"""Environment-driven configuration.

Every runtime knob is an environment variable so the container image stays
immutable across environments. Secrets (API keys) are read from either
``<NAME>`` directly or ``<NAME>_FILE`` (Docker secrets convention).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _read_secret(name: str, default: str = "") -> str:
    """Return the secret from NAME_FILE (Docker secret) or NAME env var."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ExchangeConfig:
    """Credentials and fee schedule for one venue."""

    exchange_id: str          # ccxt exchange id, e.g. "binance", "kraken"
    api_key: str
    api_secret: str
    api_password: str         # some venues (okx, kucoin) need a passphrase
    taker_fee: float          # decimal, e.g. 0.001 == 10 bps
    maker_fee: float


@dataclass(frozen=True)
class Settings:
    # --- strategy ---
    symbols: tuple[str, ...]
    min_edge_bps: float
    max_notional_per_trade: float   # quote units (e.g. USDT)
    book_max_age_ms: int
    book_depth: int
    trade_cooldown_s: float

    # --- rebalancing ---
    rebalance_skew_threshold: float
    rebalance_interval_s: float
    enable_withdrawals: bool
    profit_sweep_address: str       # non-custodial wallet for profit sweeps
    profit_sweep_network: str
    profit_sweep_min_quote: float

    # --- runtime ---
    dry_run: bool
    log_level: str
    health_port: int
    state_dir: str
    backoff_base_s: float
    backoff_cap_s: float

    exchanges: tuple[ExchangeConfig, ...] = field(default_factory=tuple)


def _load_exchanges() -> tuple[ExchangeConfig, ...]:
    """Parse EXCHANGES="binance,kraken" plus per-venue key env vars.

    For each id X (upper-cased) reads: X_API_KEY, X_API_SECRET,
    X_API_PASSWORD (optional), X_TAKER_FEE, X_MAKER_FEE.
    """
    ids = [e.strip().lower() for e in os.environ.get("EXCHANGES", "").split(",") if e.strip()]
    out: list[ExchangeConfig] = []
    for ex_id in ids:
        prefix = ex_id.upper()
        out.append(
            ExchangeConfig(
                exchange_id=ex_id,
                api_key=_read_secret(f"{prefix}_API_KEY"),
                api_secret=_read_secret(f"{prefix}_API_SECRET"),
                api_password=_read_secret(f"{prefix}_API_PASSWORD"),
                taker_fee=_env_float(f"{prefix}_TAKER_FEE", 0.001),
                maker_fee=_env_float(f"{prefix}_MAKER_FEE", 0.001),
            )
        )
    return tuple(out)


def load_settings() -> Settings:
    symbols = tuple(
        s.strip().upper() for s in os.environ.get("SYMBOLS", "BTC/USDT").split(",") if s.strip()
    )
    return Settings(
        symbols=symbols,
        min_edge_bps=_env_float("MIN_EDGE_BPS", 8.0),
        max_notional_per_trade=_env_float("MAX_NOTIONAL_PER_TRADE", 500.0),
        book_max_age_ms=_env_int("BOOK_MAX_AGE_MS", 750),
        book_depth=_env_int("BOOK_DEPTH", 25),
        trade_cooldown_s=_env_float("TRADE_COOLDOWN_S", 1.0),
        rebalance_skew_threshold=_env_float("REBALANCE_SKEW_THRESHOLD", 0.35),
        rebalance_interval_s=_env_float("REBALANCE_INTERVAL_S", 300.0),
        enable_withdrawals=_env_bool("ENABLE_WITHDRAWALS", False),
        profit_sweep_address=_read_secret("PROFIT_SWEEP_ADDRESS"),
        profit_sweep_network=os.environ.get("PROFIT_SWEEP_NETWORK", ""),
        profit_sweep_min_quote=_env_float("PROFIT_SWEEP_MIN_QUOTE", 1000.0),
        dry_run=_env_bool("DRY_RUN", True),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        health_port=_env_int("HEALTH_PORT", 8080),
        state_dir=os.environ.get("STATE_DIR", "./state"),
        backoff_base_s=_env_float("BACKOFF_BASE_S", 1.0),
        backoff_cap_s=_env_float("BACKOFF_CAP_S", 60.0),
        exchanges=_load_exchanges(),
    )
