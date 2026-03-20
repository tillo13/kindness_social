"""
Claude LLM Backend - haiku/sonnet/opus via Anthropic API.
Uses existing KUMORI_ANTHROPIC_API_KEY from Secret Manager.
"""

import logging
from anthropic import Anthropic
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

# Model tier mapping
MODEL_TIERS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus": "claude-opus-4-5-20251101",
}

_clients = {}


def _get_client(tier="haiku"):
    if tier not in _clients:
        api_key = get_secret('KUMORI_ANTHROPIC_API_KEY')
        if not api_key:
            raise RuntimeError("KUMORI_ANTHROPIC_API_KEY not found")
        _clients[tier] = Anthropic(api_key=api_key, timeout=120, max_retries=2)
    return _clients[tier]


def chat(messages, max_tokens=500, temperature=0.3, system=None, tier="haiku"):
    """
    Generate text via Claude API.
    messages: list of {"role": "user"|"assistant"|"system", "content": "..."}
    tier: "haiku", "sonnet", or "opus"
    Returns: string response
    """
    client = _get_client(tier)
    model = MODEL_TIERS.get(tier, MODEL_TIERS["haiku"])

    # Extract system from messages if not provided
    system_text = system or ""
    api_messages = []
    for msg in messages:
        if msg['role'] == 'system':
            system_text = msg['content']
        else:
            api_messages.append({"role": msg['role'], "content": msg['content']})

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_text if system_text else "",
            messages=api_messages,
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude ({tier}) error: {e}")
        raise
