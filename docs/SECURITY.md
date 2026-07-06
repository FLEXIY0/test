# Security & Credential Management Guide

## 1. Threat model

The single asset that matters is the set of exchange API keys. A leaked
trade-scoped key lets an attacker churn your balance; a leaked
withdrawal-scoped key lets them drain it. Every control below exists to keep
keys out of images, logs, shell history, and git.

## 2. Key issuance policy (do this on the exchange side first)

1. **Two keys per venue, not one.**
   - *Trading key*: permissions `read` + `trade` only. Used by the daemon's
     hot path.
   - *Rebalancing key*: `withdraw` permission, **address-allowlisted** to the
     exact deposit addresses of your other venues and your profit-sweep
     wallet. Only mounted if `ENABLE_WITHDRAWALS=true`.
2. **IP-allowlist every key** to the static IP of the VPS. A stolen key is
   then useless off-box.
3. Withdrawal allowlisting means even the rebalancing key cannot send funds
   to an attacker-controlled address.
4. Rotate keys quarterly; revoke immediately on any anomaly in the journal.

## 3. Secret delivery: Docker secrets, never environment files

The daemon reads each credential from `<NAME>_FILE` before falling back to a
plain env var (`arbitrage/config.py::_read_secret`). Compose mounts secrets
at `/run/secrets/<name>` on a tmpfs, so keys never touch the image, `.env`,
`docker inspect` output, or disk.

```bash
mkdir -p secrets && chmod 700 secrets
umask 077
printf '%s' 'AKxxxxxxxx' > secrets/binance_api_key
printf '%s' 'SKxxxxxxxx' > secrets/binance_api_secret
printf '%s' 'AKyyyyyyyy' > secrets/kraken_api_key
printf '%s' 'SKyyyyyyyy' > secrets/kraken_api_secret
```

Notes:
- `printf` instead of `echo` avoids a trailing newline; `umask 077` keeps the
  files owner-readable only.
- `secrets/` and `.env` are in `.gitignore`. Verify with `git status` before
  every commit; run a secret scanner (e.g. `gitleaks detect`) in CI.
- On Docker Swarm / Kubernetes, replace file-backed secrets with
  `docker secret create` / a `Secret` object — the code path is identical.

## 4. Container hardening (already wired in docker-compose.yml)

| Control | Effect |
|---|---|
| `read_only: true` + named volume for `/app/state` | rootfs immutable; only the journal is writable |
| `cap_drop: ALL`, `no-new-privileges` | no capability escalation from inside |
| non-root `UID 10001` user in the Dockerfile | container escape lands unprivileged |
| health port bound to `127.0.0.1` | probe endpoint never exposed publicly |
| log rotation (`max-size`/`max-file`) | disk cannot fill from logging |

## 5. Host / VPS checklist

- Dedicated VPS for the bot; nothing else runs on it.
- SSH: key-only auth, no root login, `fail2ban` or equivalent.
- Unattended security updates enabled.
- Firewall (ufw/nftables): allow outbound 443 only + inbound SSH from your
  admin IP; the daemon needs **zero** inbound ports.
- NTP synced (`systemd-timesyncd`) — exchanges reject requests with skewed
  timestamps and signed requests embed a nonce.

## 6. Operational safety rails in the code

- `DRY_RUN=true` is the default: full pipeline runs against live market data
  with simulated fills and an identical journal, so you validate edge
  detection and PnL accounting before any order is live.
- `ENABLE_WITHDRAWALS=false` is the default: the rebalancer only *reports*
  skew until you consciously enable transfers.
- Write-ahead journal (`state/journal.ndjson`) records intent before
  submission; startup reconciliation cancels stray open orders after a crash.
- `MAX_NOTIONAL_PER_TRADE` hard-caps per-trade exposure regardless of what
  the engine computes.
