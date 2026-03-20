"""
Kindness Social Usage Limiter
Per-backend daily request counters with kill switch.
"""

import os
import logging
from datetime import date
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Per-backend daily limits (free tier caps)
# Per-backend daily limits and cost info
BACKEND_INFO = {
    'gemini': {
        'daily_limit': 1400,
        'cost_per_1k_tokens': 0.0,
        'notes': 'Gemini 2.0 Flash free tier: 1500 req/day',
        'tier': 'free',
    },
    'grok': {
        'daily_limit': 500,
        'cost_per_1k_tokens': 0.0,
        'notes': 'Grok zero-auth free tier',
        'tier': 'free',
    },
    'deepseek': {
        'daily_limit': 1000,
        'cost_per_1k_tokens': 0.00014,
        'notes': 'DeepSeek Chat: 5M free tokens on signup, then $0.14/M input',
        'tier': 'free_credits',
    },
    'haiku': {
        'daily_limit': 500,
        'cost_per_1k_tokens': 0.001,
        'notes': 'Claude Haiku 4.5 via Max plan',
        'tier': 'paid',
    },
    'sonnet': {
        'daily_limit': 100,
        'cost_per_1k_tokens': 0.003,
        'notes': 'Claude Sonnet 4.5 via Max plan',
        'tier': 'paid',
    },
    'opus': {
        'daily_limit': 20,
        'cost_per_1k_tokens': 0.015,
        'notes': 'Claude Opus 4.5 via Max plan',
        'tier': 'paid',
    },
    'gpt4o_mini': {
        'daily_limit': 500,
        'cost_per_1k_tokens': 0.00015,
        'notes': 'GPT-4o Mini: $5 free credits on signup (~3.3M tokens), then $0.15/M input',
        'tier': 'free_credits',
    },
    'gpt4o': {
        'daily_limit': 50,
        'cost_per_1k_tokens': 0.0025,
        'notes': 'GPT-4o: shares $5 free credits, then $2.50/M input',
        'tier': 'free_credits',
    },
    'local': {
        'daily_limit': 999999,
        'cost_per_1k_tokens': 0.0,
        'notes': 'Local LLMs via LM Studio (Qwen, Llama, etc.)',
        'tier': 'free',
    },
}

BACKEND_LIMITS = {k: v['daily_limit'] for k, v in BACKEND_INFO.items()}

GLOBAL_DAILY_LIMIT = 3000  # Total across all backends

# In-memory counters (reset on cold start, fine for min-0 scaling)
_daily_counts = {}
_count_date = None


class UsageStatus(NamedTuple):
    allowed: bool
    count: int
    message: str
    level: str  # 'ok', 'warning', 'blocked'


def _reset_if_new_day():
    global _daily_counts, _count_date
    today = date.today()
    if _count_date != today:
        _daily_counts = {}
        _count_date = today


def is_kill_switch_active():
    return os.environ.get('KINDNESS_KILL_SWITCH', 'false').lower() == 'true'


def check_backend_ok(backend: str) -> UsageStatus:
    """Check if a specific backend is within daily limits."""
    if is_kill_switch_active():
        return UsageStatus(False, -1, "System paused for maintenance.", 'blocked')

    _reset_if_new_day()
    count = _daily_counts.get(backend, 0)
    limit = BACKEND_LIMITS.get(backend, 100)

    total = sum(_daily_counts.values())
    if total >= GLOBAL_DAILY_LIMIT:
        return UsageStatus(False, total, "Daily global limit reached.", 'blocked')

    if count >= limit:
        return UsageStatus(False, count, f"{backend} daily limit reached ({limit}).", 'blocked')

    if count >= limit * 0.8:
        return UsageStatus(True, count, "", 'warning')

    return UsageStatus(True, count, "", 'ok')


def record_usage(backend: str, count: int = 1):
    """Record API calls for a backend."""
    _reset_if_new_day()
    _daily_counts[backend] = _daily_counts.get(backend, 0) + count


def get_usage_summary() -> dict:
    """Get current usage across all backends with limit/cost info."""
    _reset_if_new_day()
    backend_details = {}
    for name, info in BACKEND_INFO.items():
        used = _daily_counts.get(name, 0)
        backend_details[name] = {
            'used': used,
            'limit': info['daily_limit'],
            'pct': round((used / info['daily_limit']) * 100, 1) if info['daily_limit'] > 0 else 0,
            'cost_per_1k': info['cost_per_1k_tokens'],
            'notes': info['notes'],
            'tier': info['tier'],
        }
    return {
        'date': date.today().isoformat(),
        'backends': backend_details,
        'total': sum(_daily_counts.values()),
        'global_limit': GLOBAL_DAILY_LIMIT,
        'kill_switch': is_kill_switch_active(),
    }
