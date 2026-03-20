"""
Local LLM Backend - via ROG Gateway (llama, qwen, etc.)
Connects to localhost:9000 or ROG:9000.
$0 cost, runs on local hardware. Gracefully skipped if unreachable.
"""

import logging
import requests

logger = logging.getLogger(__name__)

GATEWAY_HOSTS = ["http://localhost:9000", "http://ROG:9000", "http://10.0.0.100:9000"]
TIMEOUT = 120
_gateway_url = None


def _find_gateway():
    """Find a responsive gateway host."""
    global _gateway_url
    if _gateway_url:
        return _gateway_url

    for host in GATEWAY_HOSTS:
        try:
            r = requests.get(f"{host}/llm/status", timeout=5,
                             headers={"Authorization": "Bearer local"})
            if r.status_code == 200:
                _gateway_url = host
                logger.info(f"ROG Gateway found at {host}")
                return host
        except Exception:
            continue

    raise ConnectionError("ROG Gateway not reachable on any host")


def _headers():
    return {"Authorization": "Bearer local", "Content-Type": "application/json"}


def is_available():
    """Check if the local gateway is reachable."""
    try:
        _find_gateway()
        return True
    except ConnectionError:
        return False


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """
    Generate text via local LLM on ROG Gateway.
    Uses OpenAI-compatible /v1/chat/completions endpoint.
    """
    gateway = _find_gateway()

    api_messages = []
    if system:
        api_messages.append({"role": "system", "content": system})

    for msg in messages:
        api_messages.append({"role": msg['role'], "content": msg['content']})

    payload = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    try:
        r = requests.post(
            f"{gateway}/v1/chat/completions",
            json=payload,
            headers=_headers(),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"ROG Gateway error: {e}")
        raise
