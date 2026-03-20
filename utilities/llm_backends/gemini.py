"""
Gemini LLM Backend - Google AI Studio free tier (1500 req/day).
"""

import logging
from utilities.google_secret_utils import get_secret

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        import google.generativeai as genai
        api_key = get_secret('KINDNESS_GEMINI_API_KEY')
        if not api_key:
            raise RuntimeError("KINDNESS_GEMINI_API_KEY not found in secrets or env")
        genai.configure(api_key=api_key)
        _client = genai
    return _client


def chat(messages, max_tokens=500, temperature=0.3, system=None):
    """
    Generate text via Gemini.
    messages: list of {"role": "user"|"assistant"|"system", "content": "..."}
    Returns: string response
    """
    genai = _get_client()

    # Build contents for Gemini
    contents = []
    system_text = system or ""

    for msg in messages:
        if msg['role'] == 'system':
            system_text = msg['content']
        elif msg['role'] == 'user':
            contents.append({"role": "user", "parts": [msg['content']]})
        elif msg['role'] == 'assistant':
            contents.append({"role": "model", "parts": [msg['content']]})

    model = genai.GenerativeModel(
        'gemini-2.5-flash',
        system_instruction=system_text if system_text else None,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
    )

    try:
        response = model.generate_content(contents)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        raise
