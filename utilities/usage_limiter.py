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
# Actual free tier daily caps — never exceed these so we never get 429s or charges.
# When a backend hits its cap, skip it till midnight UTC. No retries, no waste.
BACKEND_INFO = {
    'gemini': {
        'daily_limit': 230,        # 250 req/day for Flash (cut Dec 2025), 10 RPM
        'cost_per_1k_tokens': 0.0,
        'notes': 'Gemini 2.5 Flash free tier: 250 req/day (slashed Dec 2025)',
        'tier': 'free',
    },
    'groq': {
        'daily_limit': 900,        # 1K RPD per model, 30 RPM, 100K tok/day
        'cost_per_1k_tokens': 0.0,
        'notes': 'Groq: Llama 3.3 70B, 1K RPD free',
        'tier': 'free',
    },
    'groq-kimi': {
        'daily_limit': 900,        # 1K RPD, 60 RPM, 300K tok/day
        'cost_per_1k_tokens': 0.0,
        'notes': 'Groq: Kimi K2 Instruct, 1K RPD free',
        'tier': 'free',
    },
    'groq-qwen': {
        'daily_limit': 900,        # 1K RPD, 60 RPM, 500K tok/day
        'cost_per_1k_tokens': 0.0,
        'notes': 'Groq: Qwen3 32B, 1K RPD free',
        'tier': 'free',
    },
    'groq-gptoss': {
        'daily_limit': 900,        # 1K RPD, 30 RPM, 200K tok/day
        'cost_per_1k_tokens': 0.0,
        'notes': 'Groq: GPT-OSS 120B, 1K RPD free',
        'tier': 'free',
    },
    'cerebras': {
        'daily_limit': 9500,       # ~1M tokens/day free, ~950 calls at 1K avg
        'cost_per_1k_tokens': 0.0,
        'notes': 'Cerebras: Llama 3.1 8B, 1M tokens/day free',
        'tier': 'free',
    },
    'together': {
        'daily_limit': 900,        # ~1000/day on free tier
        'cost_per_1k_tokens': 0.0,
        'notes': 'Together AI: Llama 3.3 70B free variant',
        'tier': 'free',
    },
    'mistral': {
        'daily_limit': 2800,       # 2 RPM = ~2,880/day, 1B tok/month
        'cost_per_1k_tokens': 0.0,
        'notes': 'Mistral Small: 2 RPM, 1B tok/month',
        'tier': 'free',
    },
    'nvidia': {
        'daily_limit': 500,        # 5K LIFETIME credits, 40 RPM — conserve!
        'cost_per_1k_tokens': 0.0,
        'notes': 'NVIDIA NIM: Llama 3.3 70B, 5K lifetime credits',
        'tier': 'free',
    },
    'llm7': {
        'daily_limit': 500,        # No documented daily cap, no key needed, 30 RPM
        'cost_per_1k_tokens': 0.0,
        'notes': 'LLM7.io: DeepSeek R1, no API key, 30 RPM',
        'tier': 'free',
    },
    'openrouter': {
        'daily_limit': 45,         # 50 req/day free (no credits), 20 RPM
        'cost_per_1k_tokens': 0.0,
        'notes': 'OpenRouter: 50/day free, 20 RPM. All :free models share this cap.',
        'tier': 'free',
    },
    'grok': {
        'daily_limit': 500,        # No official limit, PoW bypass via Cloud Run worker
        'cost_per_1k_tokens': 0.0,
        'notes': 'Grok 3 via Cloud Run worker reverse-proxy — free, no API key',
        'tier': 'free',
    },
    'deepseek': {
        'daily_limit': 500,        # No official limit, PoW bypass via Cloud Run worker
        'cost_per_1k_tokens': 0.0,
        'notes': 'DeepSeek via Cloud Run worker PoW bypass — free, unlimited',
        'tier': 'free',
    },
    'haiku': {
        'daily_limit': 500,
        'cost_per_1k_tokens': 0.001,
        'notes': 'Claude Haiku 4.5 via Max plan ($0)',
        'tier': 'max_plan',
    },
    'sonnet': {
        'daily_limit': 100,
        'cost_per_1k_tokens': 0.003,
        'notes': 'Claude Sonnet 4.5 via Max plan ($0)',
        'tier': 'max_plan',
    },
    'opus': {
        'daily_limit': 20,
        'cost_per_1k_tokens': 0.015,
        'notes': 'Claude Opus 4.5 via Max plan ($0)',
        'tier': 'max_plan',
    },
    'gpt4o_mini': {
        'daily_limit': 450,        # $5 free credits, ~3.3M tokens
        'cost_per_1k_tokens': 0.00015,
        'notes': 'GPT-4o Mini: $5 free signup credits',
        'tier': 'free_credits',
    },
    'gpt4o': {
        'daily_limit': 45,         # Shares $5 free credits, expensive per token
        'cost_per_1k_tokens': 0.0025,
        'notes': 'GPT-4o: shares $5 free credits',
        'tier': 'free_credits',
    },
    'local': {
        'daily_limit': 999999,
        'cost_per_1k_tokens': 0.0,
        'notes': 'Local LLMs via LM Studio',
        'tier': 'free',
    },
}

BACKEND_LIMITS = {k: v['daily_limit'] for k, v in BACKEND_INFO.items()}

GLOBAL_DAILY_LIMIT = 50000  # Total across all backends (effectively unlimited with proper per-backend caps)

# Shared cross-app daily caps — delegates counting to llm_usage_caps module
from utilities.llm_usage_caps import (
    check_cap as _shared_check_cap,
    record_call as _shared_record_call,
    init as _init_shared_caps,
    get_usage_summary as _shared_summary,
)

# Initialize shared caps with DB functions for cross-app sync
def _init_db_caps():
    try:
        from utilities.postgres_utils import db_cursor

        def _db_write(backend, app_name):
            with db_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO kumori_llm_daily_caps (usage_date, backend, app_name, call_count)
                    VALUES (CURRENT_DATE, %s, %s, 1)
                    ON CONFLICT (usage_date, backend, app_name)
                    DO UPDATE SET call_count = kumori_llm_daily_caps.call_count + 1
                """, (backend, app_name))

        def _db_read():
            with db_cursor(dict_cursor=True) as cur:
                cur.execute("""
                    SELECT backend, SUM(call_count) as total
                    FROM kumori_llm_daily_caps
                    WHERE usage_date = CURRENT_DATE
                    GROUP BY backend
                """)
                return {row['backend']: row['total'] for row in cur.fetchall()}

        _init_shared_caps('kindness_social', db_write_fn=_db_write, db_read_fn=_db_read)
        logger.info("Shared LLM caps initialized with DB sync")
    except Exception as e:
        logger.warning(f"Shared caps DB init failed (using local only): {e}")
        _init_shared_caps('kindness_social')

# Lazy init — runs on first usage
_caps_initialized = False

def _ensure_caps():
    global _caps_initialized
    if not _caps_initialized:
        _caps_initialized = True
        _init_db_caps()

# Backoff tracking — when a backend fails with 429/rate limit, cool it off
_backoff_until = {}  # backend -> timestamp when it's safe to retry
_backoff_count = {}  # backend -> consecutive failures (for exponential backoff)
BACKOFF_SECONDS = 120  # 2 minutes default cooldown after a rate limit


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


def mark_backend_backoff(backend: str, seconds: int = None):
    """Mark a backend as needing cooldown with exponential backoff.
    First failure: base seconds. Second: 2x. Third: 4x. Caps at 30 min."""
    import time
    _backoff_count[backend] = _backoff_count.get(backend, 0) + 1
    base = seconds or BACKOFF_SECONDS
    # Exponential: 120s -> 240s -> 480s -> 960s (cap 1800s/30min)
    cooldown = min(base * (2 ** (_backoff_count[backend] - 1)), 1800)
    _backoff_until[backend] = time.time() + cooldown
    logger.warning(f"Backend {backend} in backoff for {cooldown}s (attempt #{_backoff_count[backend]})")


def is_backend_in_backoff(backend: str) -> bool:
    """Check if a backend is in cooldown period."""
    import time
    until = _backoff_until.get(backend, 0)
    if time.time() < until:
        return True
    elif until > 0:
        # Cooldown expired, clear it
        _backoff_until.pop(backend, None)
        # DON'T reset count here — reset on success instead
    return False


def clear_backend_backoff(backend: str):
    """Reset backoff counter on successful call."""
    _backoff_until.pop(backend, None)
    _backoff_count.pop(backend, None)


def check_backend_ok(backend: str) -> UsageStatus:
    """Check if a specific backend is within daily limits and not in backoff."""
    _ensure_caps()

    if is_kill_switch_active():
        return UsageStatus(False, -1, "System paused for maintenance.", 'blocked')

    # Check backoff first (per-instance, not shared)
    if is_backend_in_backoff(backend):
        return UsageStatus(False, -1, f"{backend} in cooldown after rate limit.", 'blocked')

    # Check shared cross-app daily cap
    if not _shared_check_cap(backend):
        return UsageStatus(False, -1, f"{backend} daily cap reached.", 'blocked')

    return UsageStatus(True, 0, "", 'ok')


def record_usage(backend: str, count: int = 1):
    """Record API calls for a backend (shared cross-app counter)."""
    _ensure_caps()
    for _ in range(count):
        _shared_record_call(backend)


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
