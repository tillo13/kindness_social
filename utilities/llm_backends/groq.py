"""
Groq LLM Backend - Ultra-fast inference, 14,400 req/day free.
Supports Llama, Mixtral, Gemma models.
"""

import logging
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
_api_key = None

# Default to llama-3.3-70b — fast and capable
DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _get_key():
    global _api_key
    if _api_key is None:
        _api_key = get_secret('KINDNESS_GROQ_API_KEY')
        if not _api_key:
            raise RuntimeError("KINDNESS_GROQ_API_KEY not found")
    return _api_key


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """Generate text via Groq API (OpenAI-compatible)."""
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
    }

    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq error: {e}")
        raise
