"""
Grok LLM Backend - Zero-auth free REST API (from dr_nick pattern).
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

GROK_API_URL = "https://grok.com/rest/app-chat/conversations/new"
TIMEOUT = 60


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """
    Generate text via Grok zero-auth REST API.
    messages: list of {"role": "user"|"assistant"|"system", "content": "..."}
    Returns: string response
    """
    # Combine all messages into a single prompt for Grok
    prompt_parts = []
    if system:
        prompt_parts.append(f"System: {system}")
    for msg in messages:
        if msg['role'] == 'system':
            prompt_parts.append(f"System: {msg['content']}")
        else:
            prompt_parts.append(msg['content'])

    full_prompt = "\n\n".join(prompt_parts)

    payload = {
        "temporary": True,
        "modelName": "grok-3",
        "message": full_prompt,
        "fileAttachments": [],
        "imageAttachments": [],
        "disableSearch": True,
        "enableImageGeneration": False,
        "returnImageBytes": False,
        "returnRawGrokInXReceipts": False,
        "enableReasoning": False,
    }

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    try:
        resp = requests.post(
            GROK_API_URL,
            json=payload,
            headers=headers,
            timeout=TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()

        # Parse streaming response — collect all token chunks
        full_text = ""
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                data = json.loads(line)
                token = data.get("result", {}).get("response", {}).get("token", "")
                if token:
                    full_text += token
            except (json.JSONDecodeError, KeyError):
                continue

        if full_text:
            return full_text.strip()

        raise RuntimeError("Empty response from Grok")

    except Exception as e:
        logger.error(f"Grok error: {e}")
        raise
