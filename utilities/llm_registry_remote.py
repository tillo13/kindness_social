"""Drop-in replacement for the old `utilities.backend_registry` module-level
constants (AVAILABLE_BACKENDS / BACKEND_NAMING / MODELS / etc).

Fetches the registry from kumori at import time AND refreshes hourly via the
HTTP client (`kumori_api_client.llm_registry()`). New endpoints wired into
kumori become eligible for kindness assignment within one TTL window, with
NO redeploy required.

Mutable containers are refreshed in place so existing imports keep working:

    # legacy callers — still work, get fresh data because lists/sets/dicts
    # are mutated in place rather than rebound
    from utilities.llm_registry_remote import AVAILABLE_BACKENDS, BACKEND_NAMING

    # new callers that want an explicit fresh snapshot
    from utilities.llm_registry_remote import get_registry
    r = get_registry()  # triggers refresh-if-stale, returns dict snapshot

Scalars (CLOUD_RUN_WORKER_URL, FREE_MODEL_COUNT, MODELS_SOURCE) are
boot-time snapshots — callers needing fresh values should use get_registry().

If the HTTP call fails the previous snapshot stays in effect (fail-stale).
On first-load failure the module exposes empty defaults so the app still
boots — broken admin pages surface the issue. Failures advance the
next-attempt clock by 120s instead of the full TTL so transient hiccups
recover quickly.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_REFRESH_TTL_SEC = 3600     # success cadence — 1h matches kumori catalog refresh
_FAIL_RETRY_SEC = 120       # failure cadence — back off briefly, don't hammer

# Mutable containers — callers import these by name and the references stay
# valid across refreshes because we mutate in place.
BACKENDS: dict = {}
MODELS: list = []
AVAILABLE_BACKENDS: list = []
BACKEND_NAMING: dict = {}
FALLBACK_ORDER: list = []
LITELLM_BACKENDS: list = []
CLOUD_RUN_ONLY: set = set()

# Scalars — rebound at refresh, but callers should use get_registry() if they
# need fresh values. (Module-level scalar imports capture the value at import
# time and don't see later rebinds — that's a Python language constraint, not
# a bug here.)
CLOUD_RUN_WORKER_URL: str = ''
FREE_MODEL_COUNT: int = 0
MODELS_SOURCE: str = 'remote_not_yet_loaded'

# Cache state
_next_attempt_ts: float = 0.0       # earliest time we'll try a refresh again
_last_success_ts: float = 0.0       # last successful fetch (for diagnostics)
_refresh_lock = threading.Lock()


def _apply_registry(reg: dict) -> tuple[list, list]:
    """Mutate module-level containers in place from a freshly-fetched registry.
    Returns (added_backends, removed_backends) for the diff log.
    """
    global CLOUD_RUN_WORKER_URL, FREE_MODEL_COUNT, MODELS_SOURCE
    new_available = reg.get('available_backends') or []
    old_available = set(AVAILABLE_BACKENDS)
    added = [b for b in new_available if b not in old_available]
    removed = [b for b in old_available if b not in new_available]

    BACKENDS.clear()
    BACKENDS.update(reg.get('backends') or {})
    MODELS.clear()
    MODELS.extend(reg.get('models') or [])
    AVAILABLE_BACKENDS.clear()
    AVAILABLE_BACKENDS.extend(new_available)
    BACKEND_NAMING.clear()
    BACKEND_NAMING.update(reg.get('backend_naming') or {})
    FALLBACK_ORDER.clear()
    FALLBACK_ORDER.extend(reg.get('fallback_order') or [])
    LITELLM_BACKENDS.clear()
    LITELLM_BACKENDS.extend(reg.get('litellm_backends') or [])
    CLOUD_RUN_ONLY.clear()
    CLOUD_RUN_ONLY.update(reg.get('cloud_run_only') or [])

    CLOUD_RUN_WORKER_URL = reg.get('cloud_run_worker_url') or ''
    FREE_MODEL_COUNT = reg.get('free_model_count') or 0
    MODELS_SOURCE = reg.get('models_source') or 'remote'
    return added, removed


def _attempt_refresh() -> bool:
    """Fetch the registry from kumori and apply it. Returns True on success.
    Caller holds _refresh_lock."""
    global _next_attempt_ts, _last_success_ts
    try:
        from utilities import kumori_api_client
        reg = kumori_api_client.llm_registry()
        if not reg or not reg.get('ok'):
            logger.warning(f"llm_registry_remote: kumori returned non-ok payload: {reg}")
            _next_attempt_ts = time.time() + _FAIL_RETRY_SEC
            return False
        # Fail-stale, not fail-empty: an ok payload that carries an empty
        # available_backends (kumori mid-deploy / transient catalog blip) must
        # NOT wipe a previously-good pool — that empties AVAILABLE_BACKENDS and
        # cascades into agents created with NULL backends + birth/invite crashes.
        # Keep last-known-good and retry soon. (First-load with no prior pool
        # still applies the empty so the app boots and admin pages surface it.)
        if not reg.get('available_backends') and AVAILABLE_BACKENDS:
            logger.warning("llm_registry_remote: ok payload had empty available_backends — "
                           "keeping last-known-good pool, retrying soon")
            _next_attempt_ts = time.time() + _FAIL_RETRY_SEC
            return False
        added, removed = _apply_registry(reg)
        _last_success_ts = time.time()
        _next_attempt_ts = _last_success_ts + _REFRESH_TTL_SEC
        if added or removed:
            logger.info(
                f"llm_registry_remote: refreshed — {len(MODELS)} models, "
                f"{len(AVAILABLE_BACKENDS)} backends "
                f"(added={added or '[]'}, removed={removed or '[]'})"
            )
        else:
            logger.debug(
                f"llm_registry_remote: refreshed — {len(AVAILABLE_BACKENDS)} backends, no diff"
            )
        return True
    except Exception as e:
        logger.warning(f"llm_registry_remote: refresh failed: {e}")
        _next_attempt_ts = time.time() + _FAIL_RETRY_SEC
        return False


def refresh_if_stale() -> bool:
    """Public entry point — refresh the cache if the TTL has elapsed.
    Non-blocking on contention: if another thread is mid-refresh, returns
    False immediately and the caller continues with the previous snapshot.
    Always safe to call. Returns True only when this call performed a
    successful refresh."""
    if time.time() < _next_attempt_ts:
        return False
    if not _refresh_lock.acquire(blocking=False):
        return False
    try:
        # Re-check inside the lock so we don't double-fetch on a herd
        if time.time() < _next_attempt_ts:
            return False
        return _attempt_refresh()
    finally:
        _refresh_lock.release()


def get_registry() -> dict:
    """Trigger refresh-if-stale and return a dict snapshot. Use this when
    you need scalars (CLOUD_RUN_WORKER_URL etc.) that don't survive a rebind
    on the import side, OR when you want an explicit point-in-time copy."""
    refresh_if_stale()
    return {
        'backends': dict(BACKENDS),
        'models': list(MODELS),
        'available_backends': list(AVAILABLE_BACKENDS),
        'backend_naming': dict(BACKEND_NAMING),
        'fallback_order': list(FALLBACK_ORDER),
        'litellm_backends': list(LITELLM_BACKENDS),
        'cloud_run_only': set(CLOUD_RUN_ONLY),
        'cloud_run_worker_url': CLOUD_RUN_WORKER_URL,
        'free_model_count': FREE_MODEL_COUNT,
        'models_source': MODELS_SOURCE,
        'last_success_ts': _last_success_ts,
        'next_attempt_ts': _next_attempt_ts,
    }


# Eager first-load on import so behavior at boot matches the previous module:
# AVAILABLE_BACKENDS etc. are populated before any caller touches them.
# Failures here leave the empty defaults in place and the app boots anyway.
with _refresh_lock:
    _attempt_refresh()
