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

# Use specific free models — auto-router is unreliable
FREE_MODELS = [
    "google/gemma-3-4b-it:free",
    "google/gemma-3n-e2b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]
DEFAULT_MODEL = FREE_MODELS[0]


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

    # Try each free model until one works
    last_error = None
    for model in FREE_MODELS:
        payload["model"] = model
        try:
            r = requests.post(API_URL, json=payload, headers=headers, timeout=30)
            if r.status_code == 429:
                logger.warning(f"OpenRouter {model} rate limited")
                last_error = RuntimeError(f"429 rate limited on {model}")
                continue
            r.raise_for_status()
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not content or not content.strip():
                logger.warning(f"OpenRouter {model} returned empty")
                last_error = RuntimeError(f"Empty response from {model}")
                continue
            return content.strip()
        except requests.exceptions.Timeout:
            logger.warning(f"OpenRouter {model} timed out")
            last_error = RuntimeError(f"Timeout on {model}")
            continue
        except Exception as e:
            logger.warning(f"OpenRouter {model} error: {e}")
            last_error = e
            continue

    logger.error(f"OpenRouter all models failed: {last_error}")
    raise last_error or RuntimeError("All OpenRouter free models failed")
