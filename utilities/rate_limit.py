"""In-process token-bucket rate limiter: burst / velocity control.

This is the FAST layer that stops a tight loop (logged-in user OR no-login room
guest) from hammering an endpoint faster than a human ever would. It is:

  - per-instance memory only (no DB, no network) -> near-zero cost, safe to call
    on every request to the shared db-f1-micro fleet without adding DB load;
  - per-instance, so it resets on deploy/cold start and an attacker spread across
    many App Engine instances is capped per-instance, not globally. That is by
    design: this layer kills single-source runaways (the common case). The
    DB-backed daily caps + the aggregate usage watcher are the cross-instance /
    distributed backstop. Defense in depth, not one wall.

Usage:
    from utilities.rate_limit import check
    ok, retry_after = check(f"nh:ai:{identity}", rate_per_sec=0.5, burst=5)
    if not ok:
        return jsonify({'rate_limited': True, 'retry_after': retry_after}), 429
"""
import time
import threading

_buckets = {}                 # key -> [tokens: float, last_refill: monotonic]
_lock = threading.Lock()
_MAX_KEYS = 50_000            # crude memory ceiling; prune stale/full buckets past this
_PRUNE_IDLE_S = 120.0


def check(key, rate_per_sec, burst):
    """Token-bucket admission test. Consumes 1 token if available.

    Returns (allowed: bool, retry_after_s: float). `rate_per_sec` is the
    steady-state refill rate; `burst` is the bucket capacity (how many calls may
    arrive back-to-back before throttling). retry_after_s is a hint for the
    client / Retry-After header when denied.
    """
    if rate_per_sec <= 0 or burst <= 0:
        return True, 0.0
    now = time.monotonic()
    with _lock:
        tokens, last = _buckets.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * rate_per_sec)
        if tokens < 1.0:
            _buckets[key] = (tokens, now)
            return False, max(0.0, (1.0 - tokens) / rate_per_sec)
        _buckets[key] = (tokens - 1.0, now)
        if len(_buckets) > _MAX_KEYS:
            _prune(now)
        return True, 0.0


def _prune(now):
    """Drop buckets that are idle and full (no information lost). Best-effort;
    called under _lock only when the table is oversized."""
    stale = [k for k, (t, ls) in _buckets.items()
             if now - ls > _PRUNE_IDLE_S]
    for k in stale:
        _buckets.pop(k, None)
