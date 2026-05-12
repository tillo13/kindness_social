"""Quality-aware backend filter — consumes kumori's /catalog/quality.json
to decide which free-LLM backends are eligible for NEW agent assignment.

Pure consumer-side filter. Does NOT touch the router hot path. Existing
agents keep their backends; only new-agent creation is affected.

Fail-open everywhere: catalog fetch error, no signal, insufficient samples
→ backend is treated as eligible. We never demote a backend based on
missing data, only on positive evidence of bad quality.

QUALITY_FLOOR threshold is conservative (30/100) — only filters backends
the catalog has demonstrated are genuinely bad over multiple samples.
"""
import logging
import time

logger = logging.getLogger(__name__)

QUALITY_FLOOR = 30          # below this, skip assignment
MIN_OK_SAMPLES = 5          # require 5+ successful probes before judging
_CACHE_TTL_SEC = 3600       # 1 hour — catalog refreshes hourly

_cache = None
_cache_ts = 0.0


def _get_catalog():
    global _cache, _cache_ts
    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL_SEC:
        return _cache
    try:
        from utilities.kumori_api_client import quality_catalog
        _cache = quality_catalog(days=7) or {}
        _cache_ts = now
        return _cache
    except Exception as e:
        logger.warning(f"quality_filter: catalog fetch failed: {e}")
        _cache = {}
        _cache_ts = now  # cache the failure too — don't retry every call
        return _cache


def should_use_backend(backend_name):
    """Returns True unless the backend has demonstrated low quality.
    Fail-open: missing data → True."""
    catalog = _get_catalog()
    if not catalog:
        return True
    for b in catalog.get('backends') or []:
        if b.get('backend') != backend_name:
            continue
        q = b.get('quality_when_works')
        n_ok = b.get('n_ok') or 0
        if q is None or n_ok < MIN_OK_SAMPLES:
            return True  # insufficient signal — fail open
        return float(q) >= QUALITY_FLOOR
    return True  # not in catalog yet — fail open


def filter_eligible(backend_names):
    """Returns the subset of `backend_names` that pass the quality filter.
    If filtering would empty the list, returns the original (fail-open)."""
    filtered = [b for b in backend_names if should_use_backend(b)]
    if not filtered:
        logger.info(f"quality_filter: all {len(backend_names)} candidates "
                    f"filtered — falling back to unfiltered (fail-open)")
        return list(backend_names)
    return filtered


def summary():
    """Diagnostic: how many backends would be filtered right now."""
    catalog = _get_catalog()
    backends = catalog.get('backends') or [] if catalog else []
    bad = [b for b in backends
           if (b.get('quality_when_works') or 999) < QUALITY_FLOOR
              and (b.get('n_ok') or 0) >= MIN_OK_SAMPLES]
    return {
        'catalog_backends_seen': len(backends),
        'would_filter_out': len(bad),
        'filter_list': [b['backend'] for b in bad],
        'cache_age_sec': int(time.time() - _cache_ts) if _cache_ts else None,
        'quality_floor': QUALITY_FLOOR,
        'min_ok_samples': MIN_OK_SAMPLES,
    }
