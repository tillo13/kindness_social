"""
Claude LLM Backend - haiku/sonnet/opus via Anthropic API.
Uses existing KUMORI_ANTHROPIC_API_KEY from Secret Manager.
"""

import logging
import time
from anthropic import Anthropic
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

# --- API usage tracking ---
APP_NAME = 'kindness_social'
_PRICING = {
    'haiku-4-5': {'input': 0.0000008, 'output': 0.000004},   # $0.80/$4 per million
    'sonnet-4-5': {'input': 0.000003, 'output': 0.000015},   # $3/$15 per million
    'sonnet-4': {'input': 0.000003, 'output': 0.000015},     # $3/$15 per million
    'opus-4-6': {'input': 0.000015, 'output': 0.000075},     # $15/$75 per million
    'opus-4-5': {'input': 0.000015, 'output': 0.000075},     # $15/$75 per million
}

def _get_pricing(model):
    m = model.lower()
    for k, v in _PRICING.items():
        if k in m: return v
    return {'input': 0.000003, 'output': 0.000015}

def log_api_usage(model, usage, feature=None, streaming=False,
                  image_count=0, user_id=None, duration_ms=None):
    """Log an API call to kumori_api_usage in a background thread.
    Never blocks the caller. Never raises."""
    import threading

    def _do_log():
        try:
            from utilities.postgres_utils import db_cursor
            pricing = _get_pricing(model)
            input_tokens = getattr(usage, 'input_tokens', None) or (usage.get('input_tokens', 0) if isinstance(usage, dict) else 0) or 0
            output_tokens = getattr(usage, 'output_tokens', None) or (usage.get('output_tokens', 0) if isinstance(usage, dict) else 0) or 0
            cache_creation = getattr(usage, 'cache_creation_input_tokens', None) or (usage.get('cache_creation_input_tokens', 0) if isinstance(usage, dict) else 0) or 0
            cache_read = getattr(usage, 'cache_read_input_tokens', None) or (usage.get('cache_read_input_tokens', 0) if isinstance(usage, dict) else 0) or 0
            thinking = getattr(usage, 'thinking_tokens', None) or (usage.get('thinking_tokens', 0) if isinstance(usage, dict) else 0) or 0
            server_tools = getattr(usage, 'server_tool_use', None) or (usage.get('server_tool_use') if isinstance(usage, dict) else None) or {}
            web_searches = getattr(server_tools, 'web_search_requests', None) or (server_tools.get('web_search_requests', 0) if isinstance(server_tools, dict) else 0) or 0
            web_fetches = getattr(server_tools, 'web_fetch_requests', None) or (server_tools.get('web_fetch_requests', 0) if isinstance(server_tools, dict) else 0) or 0
            code_exec = getattr(server_tools, 'code_execution_requests', None) or (server_tools.get('code_execution_requests', 0) if isinstance(server_tools, dict) else 0) or 0
            cost = (input_tokens * pricing['input'] + output_tokens * pricing['output']
                    + cache_creation * pricing['input'] * 1.25 + cache_read * pricing['input'] * 0.1
                    + thinking * pricing['output'] + web_searches * 0.01)
            with db_cursor() as cur:
                cur.execute("""INSERT INTO kumori_api_usage
                    (app_name, feature, model, input_tokens, output_tokens,
                     cache_creation_tokens, cache_read_tokens, thinking_tokens,
                     web_search_requests, web_fetch_requests, code_execution_requests,
                     image_count, estimated_cost_usd, streaming, user_id, duration_ms)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (APP_NAME, feature, model, input_tokens, output_tokens,
                     cache_creation, cache_read, thinking, web_searches, web_fetches,
                     code_exec, image_count, cost, streaming, user_id, duration_ms))
        except Exception as e:
            logger.warning(f"Failed to log API usage: {e}")

    threading.Thread(target=_do_log, daemon=True).start()

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


def chat(messages, max_tokens=500, temperature=0.3, system=None, tier="haiku", feature="chat"):
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
        start = time.time()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_text if system_text else "",
            messages=api_messages,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        log_api_usage(model, response.usage, feature=feature, duration_ms=elapsed_ms)
        return response.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude ({tier}) error: {e}")
        raise
