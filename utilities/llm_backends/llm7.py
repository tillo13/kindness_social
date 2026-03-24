"""
LLM7.io Backend — No API key needed, 30 RPM, OpenAI-compatible.
Free inference with no registration required.
"""

import logging
import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.llm7.io/v1/chat/completions"

DEFAULT_MODEL = "deepseek-r1"


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """Generate text via LLM7.io API (OpenAI-compatible, no key)."""
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

    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"LLM7 error: {e}")
        raise
