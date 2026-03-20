"""
LLM Router - Routes requests to the appropriate backend.
Supports: gemini, grok, haiku, sonnet, opus, local
Falls through to next available backend if one is rate-limited or errors.
"""

import logging
import time
from utilities.usage_limiter import check_backend_ok, record_usage

logger = logging.getLogger(__name__)

# Fallback order (cheapest/freest first)
FALLBACK_ORDER = ['gemini', 'groq', 'cerebras', 'mistral', 'together', 'deepseek', 'openrouter', 'gpt4o_mini', 'grok', 'haiku', 'local', 'sonnet', 'gpt4o', 'opus']

# Backend to module mapping
_BACKEND_MODULES = {}


def _get_backend_module(backend):
    """Lazy-load backend modules."""
    if backend not in _BACKEND_MODULES:
        if backend == 'gemini':
            from utilities.llm_backends import gemini
            _BACKEND_MODULES[backend] = gemini
        elif backend == 'groq':
            from utilities.llm_backends import groq as groq_backend
            _BACKEND_MODULES[backend] = groq_backend
        elif backend == 'cerebras':
            from utilities.llm_backends import cerebras
            _BACKEND_MODULES[backend] = cerebras
        elif backend == 'together':
            from utilities.llm_backends import together
            _BACKEND_MODULES[backend] = together
        elif backend == 'mistral':
            from utilities.llm_backends import mistral
            _BACKEND_MODULES[backend] = mistral
        elif backend == 'openrouter':
            from utilities.llm_backends import openrouter
            _BACKEND_MODULES[backend] = openrouter
        elif backend in ('grok', 'grok_fast', 'grok4'):
            from utilities.llm_backends import grok
            _BACKEND_MODULES[backend] = grok
        elif backend == 'deepseek':
            from utilities.llm_backends import deepseek
            _BACKEND_MODULES[backend] = deepseek
        elif backend in ('gpt4o_mini', 'gpt4o'):
            from utilities.llm_backends import openai_gpt
            _BACKEND_MODULES[backend] = openai_gpt
        elif backend in ('haiku', 'sonnet', 'opus'):
            from utilities.llm_backends import claude
            _BACKEND_MODULES[backend] = claude
        elif backend == 'local':
            from utilities.llm_backends import rog_gateway
            _BACKEND_MODULES[backend] = rog_gateway
        else:
            raise ValueError(f"Unknown backend: {backend}")
    return _BACKEND_MODULES[backend]


def chat(backend, messages, max_tokens=500, temperature=0.3, system=None):
    """
    Route a chat request to the specified backend.
    Falls through to alternatives if the primary backend is unavailable.

    Returns: (response_text, actual_backend_used)
    """
    # Try primary backend first
    backends_to_try = [backend] + [b for b in FALLBACK_ORDER if b != backend]

    for b in backends_to_try:
        # Check usage limits
        status = check_backend_ok(b)
        if not status.allowed:
            logger.info(f"Backend {b} at limit, trying next...")
            continue

        try:
            module = _get_backend_module(b)

            # Local backend: check availability
            if b == 'local':
                if not module.is_available():
                    logger.info("Local LLM not available, trying next...")
                    continue

            start = time.time()

            # Grok model variants
            if b == 'grok':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-3-auto')
            elif b == 'grok_fast':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-3-fast')
            elif b == 'grok4':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-4')
            # Claude backends need tier parameter
            elif b in ('haiku', 'sonnet', 'opus'):
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, tier=b)
            elif b == 'gpt4o_mini':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='gpt-4o-mini')
            elif b == 'gpt4o':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='gpt-4o')
            else:
                result = module.chat(messages, max_tokens, temperature,
                                     system=system)

            elapsed_ms = int((time.time() - start) * 1000)
            record_usage(b)
            logger.info(f"LLM response from {b} in {elapsed_ms}ms")
            return result, b

        except Exception as e:
            logger.warning(f"Backend {b} failed: {e}, trying next...")
            continue

    # All backends failed
    logger.error("All LLM backends failed!")
    return "I appreciate your perspective.", backend


def chat_eval(backend, prompt, system="Return ONLY a number 1-10."):
    """
    Shortcut for evaluation calls (low tokens, low temperature).
    Returns: (response_text, actual_backend_used)
    """
    messages = [{"role": "user", "content": prompt}]
    return chat(backend, messages, max_tokens=10, temperature=0.1, system=system)
