"""
LLM Router — thin wrapper around litellm_plus_router.
Preserves kindness_social's public API:
  - chat(backend, messages, ...) -> (text, backend_name)
  - chat_eval(backend, prompt, system) -> (text, backend_name)
  - set_telemetry_context(...)
  - FALLBACK_ORDER, CLOUD_RUN_ONLY constants
"""

import logging

logger = logging.getLogger(__name__)

_initialized = False


def _ensure_init():
    global _initialized
    if _initialized:
        return
    _initialized = True
    try:
        from utilities.google_secret_utils import get_secret
        from utilities.postgres_utils import db_cursor
        from utilities import litellm_plus_router

        litellm_plus_router.init(
            app_name='kindness_social',
            get_secret_fn=get_secret,
            db_cursor_fn=db_cursor,
            policy='silent',
        )
    except Exception as e:
        logger.error(f"litellm_plus_router init failed: {e}")


# Derive constants from litellm_plus_router.BACKENDS for backward compat
from utilities.litellm_plus_router import BACKENDS

FALLBACK_ORDER = [b['name'] for b in BACKENDS]
CLOUD_RUN_ONLY = {b['name'] for b in BACKENDS
                  if b.get('type') in ('grok', 'grok_fast', 'grok4', 'deepseek')}

CLOUD_RUN_WORKER_URL = 'https://kindness-worker-243380010344.us-central1.run.app'


def set_telemetry_context(agent_id=None, thread_id=None, call_type=None):
    """No-op — litellm_plus_router does not track telemetry context."""
    pass


def chat(backend, messages, max_tokens=500, temperature=0.3, system=None):
    """Route a chat request to a specific backend. No fallback.
    Returns: (response_text, actual_backend_used)."""
    _ensure_init()
    from utilities.litellm_plus_router import chat as _chat
    return _chat(backend, messages, max_tokens=max_tokens,
                 temperature=temperature, system=system,
                 caller='kindness_social')


def chat_eval(backend, prompt, system="Return ONLY a number 1-10."):
    """Evaluation call — randomized free-tier pool, low tokens.
    The `backend` arg is accepted for backward compat but not used
    (eval always uses a randomized free pool)."""
    _ensure_init()
    from utilities.litellm_plus_router import chat_eval as _chat_eval
    return _chat_eval(prompt, system=system, caller='kindness_social')
