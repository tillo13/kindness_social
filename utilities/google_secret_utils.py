"""
Google Cloud Secret Manager Utilities
Caches secrets in memory to avoid repeated API calls.
"""

import logging
import os
from google.cloud import secretmanager

logger = logging.getLogger(__name__)

_cache = {}
_client = None


def get_secret(secret_id, default=None):
    """Fetch secret from Google Secret Manager with in-memory cache."""
    global _client

    # Local dev: check env vars first
    env_val = os.environ.get(secret_id)
    if env_val:
        return env_val

    if secret_id in _cache:
        return _cache[secret_id]
    try:
        if _client is None:
            _client = secretmanager.SecretManagerServiceClient()
        name = f"projects/kumori-404602/secrets/{secret_id}/versions/latest"
        response = _client.access_secret_version(request={"name": name})
        value = response.payload.data.decode('UTF-8')
        _cache[secret_id] = value
        return value
    except Exception as e:
        logger.warning(f"Could not fetch secret {secret_id}: {e}")
        return default
