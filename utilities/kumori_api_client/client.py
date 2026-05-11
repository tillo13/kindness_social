"""Thin HTTP client for kumori.ai/api/v1/* — replaces in-process vendoring of
kumori_free_llm + kumori_free_image_generations + kumori_free_image_describe
in sibling apps (kindness_social, pilgrims_world, crab_travel, scatterbrain).

Vendored INTO each consumer's utilities/ via deploy.json shared_files. Mirrors
the proven pattern from heathers_plate/utilities/kumori_api.py.

Auth: per-consumer API key in Secret Manager. Key name resolved by env var
KUMORI_API_KEY_NAME (e.g. "KINDNESS_KUMORI_API_KEY") — set in app.yaml. The
client looks for an injected get_secret function via init(), or falls back to
KUMORI_API_KEY env var directly.

Usage in a consumer:
    from utilities.kumori_api_client import init, llm_generate, imggen_generate
    init(get_secret_fn=get_secret, api_key_name='KINDNESS_KUMORI_API_KEY')

    text, backend = llm_generate('Tell me a joke')
    img = imggen_generate('a sunset over the ocean')
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

KUMORI_BASE = os.environ.get('KUMORI_API_BASE', 'https://kumori.ai')

_get_secret_fn = None
_api_key_name = None
_api_key_cache = None


class KumoriAPIError(Exception):
    """Raised when kumori.ai/api/v1/* call fails after retry."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def init(get_secret_fn=None, api_key_name=None):
    """Inject Secret Manager fetcher + which secret holds this app's API key.

    api_key_name: e.g. 'KINDNESS_KUMORI_API_KEY' (per-consumer secret in
    kumori-404602 Secret Manager). If None, falls back to KUMORI_API_KEY
    env var.
    """
    global _get_secret_fn, _api_key_name, _api_key_cache
    _get_secret_fn = get_secret_fn
    _api_key_name = api_key_name
    _api_key_cache = None  # invalidate cache


def _api_key():
    """Resolve the API key once and cache. Order:
      1. KUMORI_API_KEY env var (overrides everything — useful for local dev)
      2. _get_secret_fn(_api_key_name) if both are set
    """
    global _api_key_cache
    if _api_key_cache:
        return _api_key_cache
    val = os.environ.get('KUMORI_API_KEY')
    if not val and _get_secret_fn and _api_key_name:
        try:
            val = _get_secret_fn(_api_key_name)
        except Exception as e:
            logger.warning(f"kumori_api_client: get_secret({_api_key_name}) failed: {e}")
    _api_key_cache = val
    return val


def _request(method, path, body=None, timeout=(5, 60), retry_on_5xx=True):
    """Generic kumori API call. Returns parsed JSON dict on success, raises
    KumoriAPIError on failure.

    timeout: (connect, read) tuple. Default 5s connect / 60s read.
    retry_on_5xx: one retry on 5xx, ConnectionError, Timeout.
    """
    key = _api_key()
    if not key:
        raise KumoriAPIError(
            'kumori_api_client not initialized — call init(get_secret_fn=..., '
            'api_key_name=...) or set KUMORI_API_KEY env var'
        )
    url = f'{KUMORI_BASE}{path}'
    headers = {'X-API-Key': key, 'Content-Type': 'application/json'}

    last_exc = None
    for attempt in (1, 2):
        try:
            r = requests.request(method, url, json=body, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt == 1 and retry_on_5xx:
                logger.warning(f"kumori {path} {type(e).__name__}, retrying")
                continue
            raise KumoriAPIError(f'Network error reaching kumori: {e}')
        # Got a response — parse JSON
        try:
            data = r.json()
        except ValueError:
            data = {'raw': r.text[:300]}
        if r.status_code == 200:
            return data
        if 500 <= r.status_code < 600 and attempt == 1 and retry_on_5xx:
            logger.warning(f"kumori {path} HTTP {r.status_code}, retrying")
            continue
        raise KumoriAPIError(
            f'kumori {path} HTTP {r.status_code}: {data.get("error", "unknown")}',
            status_code=r.status_code,
            payload=data,
        )
    # Should not reach
    raise KumoriAPIError(f'kumori {path} failed after retry: {last_exc}')


# ─── LLM ──────────────────────────────────────────────────────────────────────

def llm_generate(prompt, max_tokens=500, temperature=1.0):
    """Auto-routes to a free backend. Returns (text, backend_name)."""
    data = _request('POST', '/api/v1/llm/generate',
                    {'prompt': prompt, 'max_tokens': max_tokens, 'temperature': temperature})
    return data.get('text'), data.get('backend')


def llm_chat(backend_name, messages, max_tokens=500, temperature=0.3, system=None):
    """Pinned-backend multi-turn chat. Returns (text, backend_name)."""
    body = {'backend': backend_name, 'messages': messages,
            'max_tokens': max_tokens, 'temperature': temperature}
    if system:
        body['system'] = system
    data = _request('POST', '/api/v1/llm/chat', body)
    return data.get('text'), data.get('backend')


def llm_chat_eval(prompt, system=None, caller=None):
    """Eval-pool scoring call. Signature mirrors kumori_free_llms.chat_eval.
    Returns (text, backend_name)."""
    body = {'prompt': prompt}
    if system:
        body['system'] = system
    data = _request('POST', '/api/v1/llm/chat-eval', body)
    return data.get('text'), data.get('backend')


def llm_backends():
    """List available backends. Returns the list of backend dicts."""
    data = _request('GET', '/api/v1/llm/backends')
    return data.get('backends', [])


def llm_usage():
    """Cluster-wide usage summary across backends."""
    data = _request('GET', '/api/v1/llm/usage')
    return data.get('usage', {})


def llm_registry():
    """Full backend_registry snapshot. Returns dict with keys: backends, models,
    fallback_order, cloud_run_only, litellm_backends, cloud_run_worker_url,
    available_backends, backend_naming, free_model_count, models_source."""
    data = _request('GET', '/api/v1/llm/registry')
    # Strip 'ok' key, return the rest
    return {k: v for k, v in data.items() if k != 'ok'}


def llm_backoff_state():
    """Current per-backend backoff state. Returns
    {backend_name: {until_ts, remaining_sec, backed_off}}."""
    data = _request('GET', '/api/v1/llm/backoff-state')
    return data.get('backoff_state', {})


def llm_is_backed_off(backend_name):
    """Convenience: True if backend is currently in backoff."""
    state = llm_backoff_state()
    return state.get(backend_name, {}).get('backed_off', False)


def llm_backoff_until():
    """Legacy-compat: returns {backend_name: until_timestamp} — same shape as
    the old kumori_free_llms._backoff_until dict that some callers iterate."""
    state = llm_backoff_state()
    return {name: data['until_ts'] for name, data in state.items()}


# ─── Image generation ─────────────────────────────────────────────────────────

def imggen_generate(prompt, width=1024, height=1024, skip=None):
    """Generate an image via free providers. Returns the result dict
    (typically {image_b64, provider, ms, attempts})."""
    body = {'prompt': prompt, 'width': width, 'height': height}
    if skip:
        body['skip'] = skip
    data = _request('POST', '/api/v1/imggen/generate', body, timeout=(5, 90))
    return data


# ─── Image describe ───────────────────────────────────────────────────────────

def describe_image(image_url=None, image_b64=None, prompt=None, mime=None, skip=None):
    """Describe an image via free vision LLMs. Pass either image_url OR
    image_b64. Returns {text, backend, ms, attempts}."""
    if not image_url and not image_b64:
        raise ValueError('describe_image requires image_url or image_b64')
    body = {}
    if image_url:
        body['image_url'] = image_url
    else:
        body['image_b64'] = image_b64
    if prompt:
        body['prompt'] = prompt
    if mime:
        body['mime'] = mime
    if skip:
        body['skip'] = skip
    data = _request('POST', '/api/v1/describe/describe', body, timeout=(5, 60))
    return data
