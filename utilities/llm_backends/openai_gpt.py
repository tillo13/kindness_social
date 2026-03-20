"""
OpenAI/ChatGPT LLM Backend.
Uses KINDNESS_OPENAI_API_KEY from Secret Manager.
Supports gpt-4o-mini (cheap) and gpt-4o.
"""

import logging
import requests
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
_api_key = None

# Model mapping — gpt4o_mini is the default (cheap), gpt4o for premium
MODEL_MAP = {
    'gpt4o_mini': 'gpt-4o-mini',
    'gpt4o': 'gpt-4o',
}


def _get_key():
    global _api_key
    if _api_key is None:
        _api_key = get_secret('KINDNESS_OPENAI_API_KEY')
        if not _api_key:
            raise RuntimeError("KINDNESS_OPENAI_API_KEY not found")
    return _api_key


def chat(messages, max_tokens=500, temperature=0.3, system=None, model='gpt-4o-mini'):
    """
    Generate text via OpenAI API.
    """
    api_messages = []
    if system:
        api_messages.append({"role": "system", "content": system})

    for msg in messages:
        api_messages.append({"role": msg['role'], "content": msg['content']})

    payload = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {_get_key()}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        raise
