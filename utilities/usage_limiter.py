"""
Kindness Social Usage Limiter — thin shim over litellm_plus_router.

Rate limiting, daily caps, and backoff are now handled centrally by
litellm_plus_router.py. This module exists only for backward compat
with call sites that import directly from it.
"""

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


class UsageStatus(NamedTuple):
    allowed: bool
    count: int
    message: str
    level: str  # 'ok', 'warning', 'blocked'


def _ensure_router():
    """Make sure litellm_plus_router is initialized (via llm_router wrapper)."""
    from utilities.llm_router import _ensure_init
    _ensure_init()


def is_backend_in_backoff(backend: str) -> bool:
    _ensure_router()
    from utilities.litellm_plus_router import _is_backed_off
    return _is_backed_off(backend)


def check_backend_ok(backend: str) -> UsageStatus:
    _ensure_router()
    from utilities.litellm_plus_router import _is_enabled, _is_backed_off, _rpm_ok, _check_daily_cap
    if _is_backed_off(backend):
        return UsageStatus(False, -1, f"{backend} in cooldown.", 'blocked')
    if not _is_enabled(backend):
        return UsageStatus(False, -1, f"{backend} disabled.", 'blocked')
    if not _rpm_ok(backend):
        return UsageStatus(False, -1, f"{backend} at RPM limit.", 'blocked')
    if not _check_daily_cap(backend):
        return UsageStatus(False, -1, f"{backend} daily cap reached.", 'blocked')
    return UsageStatus(True, 0, "", 'ok')


def record_usage(backend: str, count: int = 1):
    _ensure_router()
    from utilities.litellm_plus_router import _record_call
    for _ in range(count):
        _record_call(backend)


def mark_backend_backoff(backend: str, seconds: int = None):
    _ensure_router()
    from utilities.litellm_plus_router import _mark_backoff
    _mark_backoff(backend)


def clear_backend_backoff(backend: str):
    _ensure_router()
    from utilities.litellm_plus_router import _backoff_until
    _backoff_until.pop(backend, None)


def record_rpm_call(backend: str):
    _ensure_router()
    from utilities.litellm_plus_router import _rpm_record
    _rpm_record(backend)


# Expose _backoff_until dict for backward compat (app.py:1606 accesses it directly)
def __getattr__(name):
    if name == '_backoff_until':
        _ensure_router()
        from utilities.litellm_plus_router import _backoff_until
        return _backoff_until
    raise AttributeError(f"module 'usage_limiter' has no attribute {name}")


def get_usage_summary() -> dict:
    _ensure_router()
    from utilities.litellm_plus_router import get_usage_summary as _get
    return _get()


def get_cerebras_burn_rate():
    """Query historical Cerebras usage from kumori_llm_daily_caps."""
    try:
        from utilities.postgres_utils import db_cursor

        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT usage_date, app_name, call_count
                FROM kumori_llm_daily_caps
                WHERE backend = 'cerebras'
                ORDER BY usage_date DESC
                LIMIT 90
            """)
            rows = cur.fetchall()

        if not rows:
            return {'error': 'No cerebras usage data found', 'days': []}

        by_date = {}
        for row in rows:
            d = str(row['usage_date'])
            if d not in by_date:
                by_date[d] = {'total': 0, 'apps': {}}
            by_date[d]['total'] += row['call_count']
            by_date[d]['apps'][row['app_name']] = row['call_count']

        dates_sorted = sorted(by_date.keys(), reverse=True)
        daily_totals = [by_date[d]['total'] for d in dates_sorted]
        total_calls = sum(daily_totals)
        num_days = len(dates_sorted)
        avg_per_day = round(total_calls / num_days, 1) if num_days else 0
        est_tokens_per_call = 1000
        est_remaining_tokens = 3_000_000
        est_remaining_calls = est_remaining_tokens // est_tokens_per_call
        est_days_left = round(est_remaining_calls / avg_per_day, 1) if avg_per_day > 0 else float('inf')

        return {
            'status': 'CONSERVATION MODE — 90% of free tier exhausted',
            'total_calls_tracked': total_calls,
            'days_tracked': num_days,
            'avg_calls_per_day': avg_per_day,
            'est_remaining_calls': est_remaining_calls,
            'est_days_left_at_current_rate': est_days_left,
            'daily_breakdown': [
                {'date': d, 'calls': by_date[d]['total'], 'apps': by_date[d]['apps']}
                for d in dates_sorted[:30]
            ],
        }
    except Exception as e:
        logger.error(f"Cerebras burn rate query failed: {e}")
        return {'error': str(e)}
