"""
DeepSeek LLM Backend — routes through Cloud Run worker PoW bypass.
Never uses the paid api.deepseek.com endpoint.
"""

import logging
import requests

logger = logging.getLogger(__name__)

WORKER_URL = 'https://kindness-worker-243380010344.us-central1.run.app/chat'


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """
    Generate text via DeepSeek through Cloud Run worker (free, PoW bypass).
    """
    # Build the prompt from messages
    prompt = messages[-1].get('content', '') if messages else ''

    try:
        r = requests.post(
            WORKER_URL,
            json={
                'backend': 'deepseek',
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': temperature,
                'system': system,
            },
            timeout=60,
        )
        if r.ok:
            text = r.json().get('text', '')
            if text:
                return text.strip()
        logger.warning(f"DeepSeek worker returned {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"DeepSeek error: {e}")
        raise
