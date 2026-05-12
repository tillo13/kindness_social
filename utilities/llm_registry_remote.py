"""Drop-in replacement for the old `utilities.backend_registry` module-level
constants (AVAILABLE_BACKENDS / BACKEND_NAMING / MODELS / etc).

Fetches the registry from kumori once at import time via the HTTP client
(`kumori_api_client.llm_registry()`), exposes the same names so consumer
imports stay one-line swaps:

    # old:  from utilities.backend_registry import AVAILABLE_BACKENDS, BACKEND_NAMING
    # new:  from utilities.llm_registry_remote import AVAILABLE_BACKENDS, BACKEND_NAMING

If the HTTP call fails at boot (no network, kumori down, key missing) the
module logs a warning and exposes empty defaults so the app still boots —
broken admin pages surface the issue. Restart picks up a healed kumori.
"""

import logging

logger = logging.getLogger(__name__)

# Defaults if registry fetch fails. Same shape as the real backend_registry
# pre-build at the bottom of that file.
BACKENDS: dict = {}
MODELS: list = []
AVAILABLE_BACKENDS: list = []
BACKEND_NAMING: dict = {}
FALLBACK_ORDER: list = []
LITELLM_BACKENDS: list = []
CLOUD_RUN_ONLY: set = set()
CLOUD_RUN_WORKER_URL: str = ''
FREE_MODEL_COUNT: int = 0
MODELS_SOURCE: str = 'remote_failed'

try:
    from utilities import kumori_api_client
    _registry = kumori_api_client.llm_registry()
    if _registry and _registry.get('ok'):
        BACKENDS = _registry.get('backends') or {}
        MODELS = _registry.get('models') or []
        AVAILABLE_BACKENDS = _registry.get('available_backends') or []
        BACKEND_NAMING = _registry.get('backend_naming') or {}
        FALLBACK_ORDER = _registry.get('fallback_order') or []
        LITELLM_BACKENDS = _registry.get('litellm_backends') or []
        CLOUD_RUN_ONLY = set(_registry.get('cloud_run_only') or [])
        CLOUD_RUN_WORKER_URL = _registry.get('cloud_run_worker_url') or ''
        FREE_MODEL_COUNT = _registry.get('free_model_count') or 0
        MODELS_SOURCE = _registry.get('models_source') or 'remote'
        logger.info(
            f"llm_registry_remote: loaded {len(MODELS)} models, "
            f"{len(AVAILABLE_BACKENDS)} backends from kumori (source={MODELS_SOURCE})"
        )
    else:
        logger.warning(f"llm_registry_remote: kumori returned non-ok payload: {_registry}")
except Exception as e:
    logger.warning(f"llm_registry_remote: failed to load from kumori at boot: {e}")
