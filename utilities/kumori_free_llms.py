"""
kumori_free_llms.py — Combined super router for all kumori apps.

Round-robin FREE backends only. RPM throttle, cross-app daily caps,
grok/deepseek via kindness-worker, LiteLLM gateway fallback for free models.

HARD RULE: NO PAID ANTHROPIC FALLBACK. Ever. The LiteLLM gateway response is
guarded — any reply resolved to claude/haiku/sonnet/opus is rejected and the
backend is treated as failed. Drift incident 2026-05-03: gateway was silently
routing ~1.5M input tokens/day to Haiku. Never again.

Lives in _infrastructure/kumori_free_llm/. Any kumori app can import and use it.
Self-contained — NO imports from utilities.* — all deps injected via init().

Usage:
    from kumori_free_llms import init, generate

    init(
        app_name='scatterbrain',
        get_secret_fn=get_secret,        # (secret_name) -> str
        db_cursor_fn=db_cursor,          # context manager yielding cursor (optional)
        log_api_usage_fn=log_api_usage,  # (model, usage, feature=) (optional, free-tier only)
    )

    text, backend = generate("hello world")
"""

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import date

try:
    # Preferred (post 2026-05-10 package layout): relative sibling import
    from .backend_registry import (
        BACKENDS, LITELLM_BACKENDS,
        FALLBACK_LIMITS as _FALLBACK_LIMITS,
        EVAL_POOL_FREE,
    )
except ImportError:
    try:
        # Legacy: vendored as utilities/backend_registry.py
        from utilities.backend_registry import (
            BACKENDS, LITELLM_BACKENDS,
            FALLBACK_LIMITS as _FALLBACK_LIMITS,
            EVAL_POOL_FREE,
        )
    except ImportError:
        # Standalone (running from kumori_free_llm/ dir directly)
        from backend_registry import (
            BACKENDS, LITELLM_BACKENDS,
            FALLBACK_LIMITS as _FALLBACK_LIMITS,
            EVAL_POOL_FREE,
        )

# Module reference for live-refresh access — backend_registry.BACKENDS is
# rebound when refresh_models() runs, but `from backend_registry import BACKENDS`
# captures the old list at import time. Reach back through the module so the
# router always sees the latest catalog state.
try:
    from . import backend_registry as _backend_registry_mod
except ImportError:
    import backend_registry as _backend_registry_mod


def _live_backends():
    """Current BACKENDS list — reaches through backend_registry module so
    refresh_models() updates take effect without restarting the instance."""
    return _backend_registry_mod.BACKENDS

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Injected dependencies — set by init()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_app_name = None
_get_secret_fn = None       # (secret_name) -> str
_db_cursor_fn = None        # context manager yielding DB cursor
_log_api_usage_fn = None    # (model, usage_dict, feature=) -> None
_litellm_url = None         # LiteLLM gateway URL
_litellm_key = None         # LiteLLM virtual key for this app
_initialized = False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Provider limits — loaded from kumori_llm_provider_limits table at init.
# Fallback defaults used if DB is unavailable.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_provider_limits = {}   # backend -> {daily_limit, rpm_spacing_sec, lifetime_limit, lifetime_used, backoff_sec, enabled, conservation}
_daily_counts = {}
_count_date = None
_last_db_sync = 0
_caps_db_write_fn = None
_caps_db_read_fn = None
_last_call_time = {}    # backend_name -> timestamp
_call_counter = 0       # round-robin rotation
_backoff_until = {}     # backend_name -> timestamp when it becomes available again
# Cluster-wide cap accounting requires fresh DB reads. 30s = at most 30s of
# cap slop across the cluster. Pre-2026-05-04 this was 300s, which let
# multi-worker / multi-instance traffic exceed configured caps by 4-6x
# (proven: mistral daily_limit=30 backends running 78-85 calls/day cluster-wide).
DB_SYNC_INTERVAL = 30

# Shared-pool caps: org-wide / account-wide caps that apply across all
# backends sharing the same `shared_pool` value.
# Loaded from kumori_llm_providers.shared_pool_daily_cap at init (post 2026-05-04
# migration). Hardcoded fallback retained for resilience if DB is unreachable.
# _check_daily_cap rejects a call if EITHER its own daily_limit is exhausted
# OR its shared_pool is exhausted.
_SHARED_POOL_DAILY_CAPS = {
    'openrouter': 50,
    'cloudflare': 10000,
    'mistral':    2880,
}


def _get_limit(backend_name, field, default=None):
    """Get a limit value for a backend, from DB cache or fallback."""
    limits = _provider_limits.get(backend_name) or _FALLBACK_LIMITS.get(backend_name) or {}
    return limits.get(field, default)


def _load_provider_limits():
    """Load all provider limits from kumori_llm_provider_limits table.
    Also refreshes _SHARED_POOL_DAILY_CAPS from kumori_llm_providers.shared_pool_daily_cap."""
    global _provider_limits, _SHARED_POOL_DAILY_CAPS
    if not _db_cursor_fn:
        return
    # Refresh shared-pool daily caps from DB (was hardcoded pre-migration)
    try:
        with _db_cursor_fn(dict_cursor=False) as cur:
            cur.execute("""
                SELECT name, shared_pool_daily_cap FROM kumori_llm_providers
                WHERE shared_pool_daily_cap IS NOT NULL
            """)
            db_caps = dict(cur.fetchall())
            if db_caps:
                _SHARED_POOL_DAILY_CAPS = db_caps
    except Exception:
        pass  # fall back to hardcoded defaults
    try:
        with _db_cursor_fn(dict_cursor=False) as cur:
            # NEW columns (cooldown_until, consecutive_failures, failure_threshold,
            # cooldown_seconds) are optional — gracefully handle pre-migration DBs.
            try:
                cur.execute("""
                    SELECT backend, daily_limit, rpm_spacing_sec, lifetime_limit, lifetime_used,
                           backoff_sec, enabled, conservation,
                           cooldown_until, consecutive_failures, failure_threshold, cooldown_seconds,
                           shared_pool, provider
                    FROM kumori_llm_provider_limits
                """)
                rows = cur.fetchall()
                for row in rows:
                    _provider_limits[row[0]] = {
                        'daily_limit': row[1],
                        'rpm_spacing_sec': float(row[2]) if row[2] else 1.0,
                        'lifetime_limit': row[3],
                        'lifetime_used': row[4] or 0,
                        'backoff_sec': row[5] or 120,
                        'enabled': row[6] if row[6] is not None else True,
                        'conservation': row[7] if row[7] is not None else False,
                        'cooldown_until_ts': row[8].timestamp() if row[8] else 0,
                        'consecutive_failures': row[9] or 0,
                        'failure_threshold': row[10] or 3,
                        'cooldown_seconds': row[11] or 60,
                        'shared_pool': row[12],
                        'provider': row[13],
                    }
            except Exception:
                # Fallback for pre-migration schema — circuit breaker degrades to no-op
                cur.execute("""
                    SELECT backend, daily_limit, rpm_spacing_sec, lifetime_limit, lifetime_used,
                           backoff_sec, enabled, conservation
                    FROM kumori_llm_provider_limits
                """)
                for row in cur.fetchall():
                    _provider_limits[row[0]] = {
                        'daily_limit': row[1],
                        'rpm_spacing_sec': float(row[2]) if row[2] else 1.0,
                        'lifetime_limit': row[3],
                        'lifetime_used': row[4] or 0,
                        'backoff_sec': row[5] or 120,
                        'enabled': row[6] if row[6] is not None else True,
                        'conservation': row[7] if row[7] is not None else False,
                        'cooldown_until_ts': 0,
                        'consecutive_failures': 0,
                        'failure_threshold': 3,
                        'cooldown_seconds': 60,
                    }
        logger.info(f"Loaded {len(_provider_limits)} provider limits from DB")
    except Exception as e:
        logger.warning(f"Could not load provider limits from DB (using fallbacks): {e}")


def _rpm_ok(backend_name):
    spacing = _get_limit(backend_name, 'rpm_spacing_sec', 1.0)
    last = _last_call_time.get(backend_name, 0)
    return (time.time() - last) >= spacing


def _rpm_record(backend_name):
    _last_call_time[backend_name] = time.time()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Daily caps + lifetime tracking
# In-memory counters + periodic DB sync for cross-app coordination.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _reset_if_new_day():
    global _daily_counts, _count_date
    today = date.today()
    if _count_date != today:
        _daily_counts = {}
        _count_date = today


def _sync_from_db():
    global _daily_counts, _last_db_sync, _count_date
    if not _caps_db_read_fn:
        return
    try:
        totals = _caps_db_read_fn()
        if totals:
            _count_date = date.today()
            _daily_counts = dict(totals)
            _last_db_sync = time.time()
            logger.debug(f"Synced LLM caps from DB: {sum(totals.values())} total calls today")
    except Exception as e:
        logger.debug(f"DB sync failed (non-fatal): {e}")
    # Also refresh provider limits on sync
    _load_provider_limits()


def _maybe_sync():
    if time.time() - _last_db_sync > DB_SYNC_INTERVAL:
        _sync_from_db()


def _is_enabled(backend_name):
    """Check if a backend is enabled in provider limits."""
    return _get_limit(backend_name, 'enabled', True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Circuit breaker — generic, provider-agnostic, applied to ALL backends.
# Pattern matches LiteLLM/Portkey/OpenRouter community consensus:
#   closed    consecutive_failures < threshold AND cooldown_until is past
#   open      cooldown_until > NOW() — _try_backend skips silently
#   half-open cooldown just expired — next real call IS the test
# Self-healing: free-tier flicker auto-recovers without operator action.
# Implementation lives in ._breaker (extracted 2026-05-10).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from ._breaker import (
    _is_in_cooldown,
    _record_breaker_success,
    _record_breaker_failure,
)
# probe_backend extracted to ._probe (re-exported here for back-compat callers)
from ._probe import probe_backend  # noqa: F401


def _check_shared_pool(backend_name):
    """If this backend has a shared_pool with a configured cap, return False
    when the pool's total cluster-wide usage today is already at/over the cap.

    Pool totals are read from kumori_llm_daily_caps (cluster-wide source of
    truth). Uses the same _maybe_sync cache window as _check_daily_cap so we
    don't hit the DB twice per call.

    Returns True (allow) when no pool is configured or the DB read fails —
    fail-open is correct here because the per-backend daily_limit + breaker
    are independent safety nets.
    """
    pool = _get_limit(backend_name, 'shared_pool')
    if not pool:
        return True
    cap = _SHARED_POOL_DAILY_CAPS.get(pool)
    if cap is None:
        return True
    if not _db_cursor_fn:
        return True
    try:
        with _db_cursor_fn(dict_cursor=False, commit=False) as cur:
            cur.execute("""
                SELECT COALESCE(SUM(d.call_count), 0)
                FROM kumori_llm_daily_caps d
                JOIN kumori_llm_provider_limits pl ON pl.backend = d.backend
                WHERE pl.shared_pool = %s AND d.usage_date = CURRENT_DATE
            """, (pool,))
            pool_used = cur.fetchone()[0] or 0
        if pool_used >= cap:
            logger.debug(f"shared_pool '{pool}' EXHAUSTED: {pool_used}/{cap} — blocking {backend_name}")
            return False
        return True
    except Exception:
        return True  # Fail-open on DB hiccup; per-backend cap + breaker still apply


def _check_daily_cap(backend_name):
    """Cluster-wide daily cap check. Reads from kumori_llm_daily_caps (the
    single source of truth across all consumer apps + workers + instances)
    via the _sync_from_db cache (DB_SYNC_INTERVAL = 30s).

    Pre-2026-05-04 this read from in-memory _daily_counts which was per-worker
    per-instance, so the per-cluster effective cap was N_workers × N_instances ×
    daily_limit (proven: mistral daily_limit=30 backends running 78-85/day).

    Now also enforces shared_pool caps (openrouter 50/day account-wide, etc.)
    so org-wide caps actually hold.
    """
    _reset_if_new_day()
    _maybe_sync()  # refreshes _daily_counts from DB if stale (>30s old)

    # Per-backend daily limit
    cap = _get_limit(backend_name, 'daily_limit', 50)
    if cap is not None:
        used = _daily_counts.get(backend_name, 0)
        if used >= cap:
            return False

    # Shared-pool cap (org-wide, e.g., openrouter 50/day across all :free)
    if not _check_shared_pool(backend_name):
        return False

    return True


def _check_lifetime(backend_name):
    """Check if a backend has lifetime budget remaining."""
    limit = _get_limit(backend_name, 'lifetime_limit')
    if limit is None:
        return True  # No lifetime limit
    used = _get_limit(backend_name, 'lifetime_used', 0)
    return used < limit


def _record_call(backend_name, usage=None):
    """Record a successful call. `usage` is optional {'in': N, 'out': N} from
    the provider response — added 2026-05-07 so kumori_llm_daily_caps.tokens_in/
    tokens_out actually populate. Pre-2026-05-07 the columns existed but were
    never written; consumer-app dashboards showed 0 tokens forever."""
    _reset_if_new_day()
    _daily_counts[backend_name] = _daily_counts.get(backend_name, 0) + 1
    # Async DB writes — daily caps + lifetime
    if _caps_db_write_fn and _app_name:
        threading.Thread(target=_safe_caps_write, args=(backend_name, usage), daemon=True).start()
    if _get_limit(backend_name, 'lifetime_limit') is not None and _db_cursor_fn:
        threading.Thread(target=_safe_lifetime_increment, args=(backend_name,), daemon=True).start()


def _safe_caps_write(backend_name, usage=None):
    try:
        _caps_db_write_fn(backend_name, _app_name, usage)
    except Exception:
        pass


def _safe_lifetime_increment(backend_name):
    """Increment lifetime_used in kumori_llm_provider_limits. Fire-and-forget."""
    try:
        with _db_cursor_fn(dict_cursor=False, commit=True) as cur:
            cur.execute("""
                UPDATE kumori_llm_provider_limits
                SET lifetime_used = lifetime_used + 1, updated_at = NOW()
                WHERE backend = %s
            """, (backend_name,))
            cur.execute("""
                UPDATE kumori_llm_endpoints
                SET lifetime_used = lifetime_used + 1, updated_at = NOW()
                WHERE backend = %s
            """, (backend_name,))
        # Update local cache too
        if backend_name in _provider_limits:
            _provider_limits[backend_name]['lifetime_used'] = _provider_limits[backend_name].get('lifetime_used', 0) + 1
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Sticky backoff — mark backends unavailable after failures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _mark_backoff(backend_name):
    duration = _get_limit(backend_name, 'backoff_sec', 120)
    _backoff_until[backend_name] = time.time() + duration
    logger.info(f"Backing off {backend_name} for {duration}s")


def _is_backed_off(backend_name):
    until = _backoff_until.get(backend_name, 0)
    if time.time() >= until:
        return False
    remaining = int(until - time.time())
    logger.debug(f"{backend_name} backed off for {remaining}s more")
    return True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Secret cache + per-provider HTTP impls live in ._backends (extracted during
# the 2026-05-10 1000-line-rule split). They reach back here for state via
# `from . import kumori_free_llms as _r`.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from ._backends import (
    _get_key,
    _usage_from_openai,
    _try_openai_compatible,
    _try_gemini,
    _try_worker,
    _try_cloudflare,
    _try_cohere,
    _try_litellm_gateway,
    _WORKER_TYPES,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _try_backend(backend, prompt, max_tokens, temperature, caller):
    """Try a single backend. Returns text on success, None on failure.

    If the direct call fails and the backend has a gateway_model configured,
    automatically retries through the LiteLLM Cloud Run gateway (bypasses
    Cloudflare blocks, gives spend tracking).
    """
    name = backend['name']

    if not _is_enabled(name):
        return None
    if _is_in_cooldown(name):
        return None  # circuit breaker is open — fall through to next backend
    if _is_backed_off(name):
        return None
    if not _rpm_ok(name):
        return None
    if not _check_daily_cap(name):
        return None
    if not _check_lifetime(name):
        return None

    start = time.time()
    direct_failed = False
    try:
        btype = backend.get('type')
        if btype == 'gemini':
            text, usage = _try_gemini(prompt, max_tokens, temperature, backend.get('gemini_model', 'gemini-2.5-flash'))
        elif btype in _WORKER_TYPES:
            text, usage = _try_worker(btype, prompt, max_tokens, temperature)
        elif btype == 'cloudflare':
            text, usage = _try_cloudflare(backend, prompt, max_tokens, temperature)
        elif btype == 'cohere':
            text, usage = _try_cohere(backend, prompt, max_tokens, temperature)
        elif btype == 'litellm':
            text, usage = _try_litellm_gateway(backend, prompt, max_tokens, temperature)
        else:
            text, usage = _try_openai_compatible(backend, prompt, max_tokens, temperature)

        ms = int((time.time() - start) * 1000)
        _rpm_record(name)

        if text:
            _record_call(name, usage)
            _record_breaker_success(name)
            logger.info(f"{'🌐' if btype == 'litellm' else '🆓'} {name} responded ({len(text)} chars, {ms}ms) caller={caller}")
            return text
        direct_failed = True

    except urllib.error.HTTPError as e:
        ms = int((time.time() - start) * 1000)
        _rpm_record(name)
        code = e.code
        if code == 429:
            _mark_backoff(name)
            # 429 means the upstream quota is exhausted — gateway uses the same key,
            # so don't bother retrying through gateway for rate limits.
            logger.warning(f"LLM {name} HTTP {code} ({ms}ms)")
            return None
        logger.warning(f"LLM {name} HTTP {code} ({ms}ms)")
        direct_failed = True

    except Exception as e:
        ms = int((time.time() - start) * 1000)
        _rpm_record(name)
        # Detect 429 in non-HTTP exceptions (e.g. Gemini SDK)
        if '429' in str(e):
            _mark_backoff(name)
            logger.warning(f"LLM {name} rate limited ({ms}ms)")
            return None  # Skip gateway fallback — same API key, same quota
        logger.warning(f"LLM {name} failed ({ms}ms): {e}")
        direct_failed = True

    # Gateway fallback: if direct call failed (not 429) and a gateway model exists,
    # retry through the LiteLLM Cloud Run gateway. This bypasses Cloudflare blocks
    # and other local network issues since the gateway runs in GCP.
    if direct_failed and backend.get('gateway_model') and _litellm_url and _litellm_key:
        gw_model = backend['gateway_model']
        logger.info(f"Retrying {name} via gateway ({gw_model})...")
        gw_start = time.time()
        try:
            text, usage = _try_litellm_gateway({'litellm_model': gw_model}, prompt, max_tokens, temperature)
            gw_ms = int((time.time() - gw_start) * 1000)
            if text:
                _record_call(name, usage)
                _record_breaker_success(name)
                logger.info(f"🌐 {name} responded VIA GATEWAY ({len(text)} chars, {gw_ms}ms) caller={caller}")
                return text
        except urllib.error.HTTPError as e:
            gw_ms = int((time.time() - gw_start) * 1000)
            code = e.code
            if code == 429:
                _mark_backoff(name)
            logger.warning(f"LLM {name} gateway fallback HTTP {code} ({gw_ms}ms)")
        except Exception as e:
            gw_ms = int((time.time() - gw_start) * 1000)
            logger.warning(f"LLM {name} gateway fallback failed ({gw_ms}ms): {e}")

    # If we got here, the call failed (and gateway fallback also failed if attempted).
    # Note: 429s already returned None above without recording breaker failure —
    # rate-limits are handled by the daily cap / RPM systems, not the breaker.
    if direct_failed:
        _record_breaker_failure(name)
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def init(app_name, get_secret_fn=None, db_cursor_fn=None,
         log_api_usage_fn=None,
         litellm_url=None, litellm_key=None,
         policy='silent', **_legacy_kwargs):
    """
    Initialize the router. Call once at app startup. FREE BACKENDS ONLY.

    Args:
        app_name: 'scatterbrain', 'kindness_social', 'crab_travel', etc.
        get_secret_fn: (secret_name) -> str. Fetches API keys from Secret Manager.
        db_cursor_fn: Optional context manager yielding a DB cursor. Enables cross-app
                      daily cap coordination via kumori_llm_daily_caps table.
        log_api_usage_fn: Optional (model, usage_dict, feature=) -> None.
        litellm_url: LiteLLM gateway URL. Auto-fetched from LITELLM_GATEWAY_URL secret if omitted.
        litellm_key: LiteLLM virtual key. Auto-fetched from SCATTERBRAIN_LITELLM_KEY secret if omitted.
        policy: only 'silent' is honored — there are no paid fallbacks in this
                build. If every free backend fails, generate() returns (None, None).
                Argument retained for caller compat.

    Legacy kwargs (anthropic_key_fn, etc.) are accepted and ignored for
    back-compat — paid Anthropic surface has been completely removed.
    """
    global _app_name, _get_secret_fn, _db_cursor_fn
    global _log_api_usage_fn, _litellm_url, _litellm_key
    global _caps_db_write_fn, _caps_db_read_fn, _initialized, _policy

    _app_name = app_name
    _get_secret_fn = get_secret_fn
    _db_cursor_fn = db_cursor_fn
    _log_api_usage_fn = log_api_usage_fn
    _policy = 'silent'  # forced — paid fallbacks no longer exist in this build

    # Load provider limits from DB (caps, RPM spacing, lifetime, backoff, enabled)
    if db_cursor_fn:
        _load_provider_limits()

    # Wire up cross-app daily caps if DB cursor is available
    if db_cursor_fn:
        def _db_write(backend, app, usage=None):
            tin = int((usage or {}).get('in') or 0)
            tout = int((usage or {}).get('out') or 0)
            with db_cursor_fn(dict_cursor=False, commit=True) as cur:
                cur.execute("""
                    INSERT INTO kumori_llm_daily_caps
                        (usage_date, backend, app_name, call_count, tokens_in, tokens_out)
                    VALUES (CURRENT_DATE, %s, %s, 1, %s, %s)
                    ON CONFLICT (usage_date, backend, app_name)
                    DO UPDATE SET
                        call_count = kumori_llm_daily_caps.call_count + 1,
                        tokens_in  = COALESCE(kumori_llm_daily_caps.tokens_in, 0)  + EXCLUDED.tokens_in,
                        tokens_out = COALESCE(kumori_llm_daily_caps.tokens_out, 0) + EXCLUDED.tokens_out
                """, (backend, app, tin, tout))

        def _db_read():
            with db_cursor_fn(dict_cursor=False) as cur:
                cur.execute("""
                    SELECT backend, SUM(call_count) as total
                    FROM kumori_llm_daily_caps
                    WHERE usage_date = CURRENT_DATE
                    GROUP BY backend
                """)
                return {row[0]: row[1] for row in cur.fetchall()}

        _caps_db_write_fn = _db_write
        _caps_db_read_fn = _db_read
        _sync_from_db()
        logger.info(f"kumori_free_llms: caps DB sync enabled for {app_name}")

        # Register this app as a consumer of the gateway. Lets the kumori
        # admin/llm-health page show "groq-kimi failing → blast radius:
        # inroads, scatterbrain, dandy" before pruning.
        try:
            with db_cursor_fn(dict_cursor=False, commit=True) as cur:
                cur.execute("""
                    INSERT INTO kumori_llm_consumer_apps (app_name, last_seen)
                    VALUES (%s, NOW())
                    ON CONFLICT (app_name) DO UPDATE SET last_seen = NOW()
                """, (app_name,))
        except Exception as e:
            logger.info(f"kumori_free_llms: consumer-app register failed (non-fatal): {e}")

    # Resolve LiteLLM gateway URL + key
    _litellm_url = litellm_url
    _litellm_key = litellm_key
    if get_secret_fn:
        if not _litellm_url:
            try:
                _litellm_url = get_secret_fn('LITELLM_GATEWAY_URL')
            except Exception:
                logger.info("LITELLM_GATEWAY_URL not found — gateway backend disabled")
        if not _litellm_key:
            # Master key first (virtual keys need gateway DB to be healthy)
            for key_name in ['LITELLM_MASTER_KEY', 'SCATTERBRAIN_LITELLM_KEY']:
                try:
                    _litellm_key = get_secret_fn(key_name)
                    if _litellm_key:
                        logger.info(f"kumori_free_llms: using {key_name} for gateway auth")
                        break
                except Exception:
                    continue
            if not _litellm_key:
                logger.info("No LiteLLM key found — gateway backend disabled")

    if _litellm_url:
        _litellm_url = _litellm_url.rstrip('/')
        logger.info(f"kumori_free_llms: gateway at {_litellm_url}")
    else:
        logger.info("kumori_free_llms: no gateway URL — direct backends only")

    _initialized = True
    logger.info(f"kumori_free_llms initialized: app={app_name}, policy={policy}, "
                f"backends={len(_live_backends())}, gateway={'yes' if _litellm_url else 'no'}, "
                f"db_caps={'yes' if db_cursor_fn else 'no'}")

    # Spin up the background refresh thread so newly discovered probationary
    # endpoints flow into the runtime BACKENDS list within ~5 min instead of
    # waiting for an App Engine instance recycle. Disabled if the env var
    # KUMORI_FREE_LLMS_REFRESH=0 (useful for tests).
    if os.environ.get('KUMORI_FREE_LLMS_REFRESH', '1') != '0':
        _start_backend_refresh_thread()


_refresh_thread = None
_refresh_interval_sec = 300  # 5 min


def refresh_backends(modality='chat'):
    """Force a re-fetch of the BACKENDS list from kumori_llm_endpoints.
    Returns True if the registry membership changed.

    Call this explicitly after admin-API mutations (POST /api/admin/endpoints,
    enable/disable) so the change takes effect within the same request cycle
    instead of waiting for the next 5-min refresh tick."""
    try:
        changed = _backend_registry_mod.refresh_models(modality=modality)
        if changed:
            _BACKEND_BY_NAME.clear()  # force re-index on next chat() call
            logger.info(f"kumori_free_llms: BACKENDS refreshed, "
                        f"now {len(_live_backends())} backends "
                        f"(modality={modality})")
        return changed
    except Exception as e:
        logger.warning(f"kumori_free_llms: refresh_backends failed: {e}")
        return False


def _start_backend_refresh_thread():
    global _refresh_thread
    if _refresh_thread and _refresh_thread.is_alive():
        return
    def _loop():
        while True:
            time.sleep(_refresh_interval_sec)
            try:
                refresh_backends()
            except Exception:
                pass  # never let the refresh thread die
    _refresh_thread = threading.Thread(target=_loop, daemon=True,
                                        name='kumori_free_llms.refresh')
    _refresh_thread.start()
    logger.info(f"kumori_free_llms: background refresh thread started "
                f"(interval={_refresh_interval_sec}s)")


# Bandit exploration — what fraction of generate() calls deliberately route
# to an under-validated probationary/active endpoint that hasn't seen real
# traffic in 24h+. Without this, the round-robin "first responsive backend
# wins" pattern starves the long tail of real-workload validation. With this,
# every endpoint in the active+probationary set sees real calls within 48h.
EXPLORATION_RATE = float(os.environ.get('KUMORI_FREE_LLMS_EXPLORATION_RATE', '0.10'))


def _pick_under_validated_backend(modality='chat'):
    """Return one backend dict whose endpoint hasn't seen real traffic in 24h+,
    or None if no candidate available. Picked uniformly at random from the
    eligible set so every starved endpoint gets fair coverage over many calls.

    Cheap: one indexed query (idx_kumori_llm_endpoints_real_traffic). Skipped
    entirely when DB cursor not wired (test/local mode)."""
    if not _db_cursor_fn:
        return None
    try:
        with _db_cursor_fn(dict_cursor=False, commit=False) as cur:
            cur.execute("""
                SELECT e.backend
                  FROM kumori_llm_endpoints e
                  LEFT JOIN kumori_models m ON m.slug = e.model
                 WHERE e.enabled = true
                   AND e.status IN ('active', 'probationary', 'revived')
                   AND COALESCE(m.modality, 'chat') = %s
                   AND (e.last_real_traffic_at IS NULL
                        OR e.last_real_traffic_at < NOW() - INTERVAL '24 hours')
                 ORDER BY RANDOM()
                 LIMIT 1
            """, (modality,))
            row = cur.fetchone()
        if not row:
            return None
        backend_name = row[0]
        for b in _live_backends():
            if b['name'] == backend_name:
                return b
        return None  # endpoint exists in DB but not in current BACKENDS list
    except Exception:
        return None


def _record_real_traffic(backend_name):
    """Stamp last_real_traffic_at on the endpoint. Fire-and-forget thread so
    it never blocks the request path. Idempotent UPDATE."""
    if not _db_cursor_fn or not backend_name:
        return
    def _do():
        try:
            with _db_cursor_fn(dict_cursor=False, commit=True) as cur:
                cur.execute("""
                    UPDATE kumori_llm_endpoints
                       SET last_real_traffic_at = NOW(),
                           last_active_at = NOW(),
                           updated_at = NOW()
                     WHERE backend = %s
                """, (backend_name,))
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def generate(prompt, max_tokens=500, temperature=1.0, caller=None):
    """
    Route a prompt through all available FREE backends.

    Returns: (text, backend_name) or (None, None) on total failure.
    No paid fallback — silence is honest.

    Bandit exploration: with probability EXPLORATION_RATE, the FIRST attempt
    is biased to an endpoint that hasn't seen real traffic in 24h+. If the
    bandit pick fails, falls through to normal round-robin so SLA is preserved.
    """
    global _call_counter
    import random

    if not _initialized:
        logger.error("kumori_free_llms.generate() called before init()")
        return None, None

    caller = caller or _app_name or 'unknown'
    backends = _live_backends()
    n = len(backends)

    # -- Bandit exploration: try one under-validated endpoint first --
    if EXPLORATION_RATE > 0 and random.random() < EXPLORATION_RATE:
        bandit_backend = _pick_under_validated_backend()
        if bandit_backend:
            text = _try_backend(bandit_backend, prompt, max_tokens, temperature, caller)
            if text:
                _record_real_traffic(bandit_backend['name'])
                return text, bandit_backend['name']
            # Bandit miss: fall through to normal round-robin (no SLA penalty)

    # -- Tier: Direct free backends (round-robin) --
    start_idx = _call_counter % n
    _call_counter += 1

    for i in range(n):
        backend = backends[(start_idx + i) % n]
        text = _try_backend(backend, prompt, max_tokens, temperature, caller)
        if text:
            _record_real_traffic(backend['name'])
            return text, backend['name']

    # -- Tier: LiteLLM gateway --
    for backend in LITELLM_BACKENDS:
        text = _try_backend(backend, prompt, max_tokens, temperature, caller)
        if text:
            return text, backend['name']

    logger.warning(f"All free backends failed — returning None (caller={caller})")
    return None, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# chat() — message-based interface (used by kindness_social agents)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Map backend names to the corresponding entry in BACKENDS / LITELLM_BACKENDS
_BACKEND_BY_NAME = {}


def _ensure_backend_index():
    if not _BACKEND_BY_NAME:
        for b in BACKENDS + LITELLM_BACKENDS:
            _BACKEND_BY_NAME[b['name']] = b


def _messages_to_prompt(messages, system=None):
    """Convert a list of chat messages to a single prompt string."""
    parts = []
    if system:
        parts.append(system)
    for msg in messages:
        content = msg.get('content', '')
        if msg.get('role') == 'system':
            parts.append(content)
        elif msg.get('role') == 'assistant':
            parts.append(f"Assistant: {content}")
        else:
            parts.append(content)
    return '\n\n'.join(parts)


def chat(backend_name, messages, max_tokens=500, temperature=0.3, system=None, caller=None):
    """
    Route a chat request to a SPECIFIC backend. No round-robin, no fallback.
    If the backend is down, rate-limited, or at cap, returns (None, backend_name).
    This is the interface kindness_social agents use — each agent is assigned one backend.

    Args:
        backend_name: e.g. 'groq', 'grok_fast', 'gemini' (free backends only)
        messages: list of {'role': ..., 'content': ...} dicts
        max_tokens: max output tokens
        temperature: sampling temperature
        system: optional system prompt
        caller: tag for logging

    Returns: (response_text, actual_backend_used)
    """
    if not _initialized:
        logger.error("kumori_free_llms.chat() called before init()")
        return None, backend_name

    _ensure_backend_index()
    caller = caller or _app_name or 'unknown'

    backend = _BACKEND_BY_NAME.get(backend_name)
    if not backend:
        logger.warning(f"Unknown backend: {backend_name}")
        return None, backend_name

    prompt = _messages_to_prompt(messages, system)
    text = _try_backend(backend, prompt, max_tokens, temperature, caller)
    if text:
        _record_real_traffic(backend_name)
        return text, backend_name

    return None, backend_name


def chat_eval(prompt, system="Return ONLY a number 1-10.", caller=None):
    """
    Evaluation call — low tokens, low temperature, randomized free-tier pool.
    Eval is a system function (scoring), not an agent voice, so it uses a
    randomized free pool for consistency. Paid backends are never used.
    Returns: (response_text, backend_name)
    """
    if not _initialized:
        logger.error("kumori_free_llms.chat_eval() called before init()")
        return None, 'eval'

    import random
    _ensure_backend_index()
    caller = caller or _app_name or 'unknown'

    pool = EVAL_POOL_FREE[:]
    random.shuffle(pool)

    eval_prompt = f"{system}\n\n{prompt}" if system else prompt

    for name in pool:
        backend = _BACKEND_BY_NAME.get(name)
        if not backend:
            continue
        text = _try_backend(backend, eval_prompt, max_tokens=10, temperature=0.1, caller=f'eval:{caller}')
        if text and text.strip():
            return text, name

    return None, 'eval'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Utilities
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_policy = 'always_answer'


def get_usage_summary():
    """Current usage vs caps for all backends."""
    _reset_if_new_day()
    # Collect all known backends from both DB limits and backend configs
    all_backends = set()
    for b in BACKENDS + LITELLM_BACKENDS:
        all_backends.add(b['name'])
    all_backends.update(_provider_limits.keys())
    all_backends.update(_FALLBACK_LIMITS.keys())

    result = {}
    for backend in sorted(all_backends):
        cap = _get_limit(backend, 'daily_limit', 50) or 0
        used = _daily_counts.get(backend, 0)
        entry = {
            'used': used, 'cap': cap,
            'remaining': max(0, cap - used),
            'pct': round(used / cap * 100, 1) if cap > 0 else 0,
            'enabled': _is_enabled(backend),
        }
        lifetime = _get_limit(backend, 'lifetime_limit')
        if lifetime:
            lifetime_used = _get_limit(backend, 'lifetime_used', 0)
            entry['lifetime_limit'] = lifetime
            entry['lifetime_used'] = lifetime_used
            entry['lifetime_remaining'] = max(0, lifetime - lifetime_used)
        result[backend] = entry

    return {
        'date': date.today().isoformat(),
        'app': _app_name,
        'policy': _policy,
        'gateway': bool(_litellm_url),
        'limits_source': 'db' if _provider_limits else 'fallback',
        'backends': result,
        'total_used': sum(_daily_counts.values()),
        'backoffs': {k: int(v - time.time()) for k, v in _backoff_until.items() if v > time.time()},
    }


def list_backends():
    """List all configured backends with their current status."""
    rows = []
    for backend in BACKENDS + LITELLM_BACKENDS:
        name = backend['name']
        cap = _get_limit(name, 'daily_limit', 50) or 0
        used = _daily_counts.get(name, 0)
        entry = {
            'name': name,
            'type': backend.get('type', 'openai'),
            'enabled': _is_enabled(name),
            'rpm_ok': _rpm_ok(name),
            'cap_ok': _check_daily_cap(name),
            'lifetime_ok': _check_lifetime(name),
            'backed_off': _is_backed_off(name),
            'cap_remaining': max(0, cap - used),
        }
        lifetime = _get_limit(name, 'lifetime_limit')
        if lifetime:
            entry['lifetime_remaining'] = max(0, lifetime - _get_limit(name, 'lifetime_used', 0))
        rows.append(entry)
    return rows
