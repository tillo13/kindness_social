"""
OpenRouter LLM Backend - Unified API to dozens of models.
Community free tier gives access to free variants of Meta, Google, Mistral models.
"""

import logging
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
_api_key = None

# Use a free community model by default
DEFAULT_MODEL = "openrouter/free"  # Auto-routes to best available free model


def _get_key():
    global _api_key
    if _api_key is None:
        _api_key = get_secret('KINDNESS_OPENROUTER_API_KEY')
        if not _api_key:
            raise RuntimeError("KINDNESS_OPENROUTER_API_KEY not found")
    return _api_key


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """Generate text via OpenRouter API (OpenAI-compatible)."""
    api_messages = []
    if system:
        api_messages.append({"role": "system", "content": system})

    for msg in messages:
        api_messages.append({"role": msg['role'], "content": msg['content']})

    payload = {
        "model": DEFAULT_MODEL,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {_get_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://kindness-io.uc.r.appspot.com",
        "X-Title": "Kindness Social",
    }

    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content or not content.strip():
            raise RuntimeError("Empty response from OpenRouter free model")
        return content.strip()
    except Exception as e:
        logger.error(f"OpenRouter error: {e}")
        raise
