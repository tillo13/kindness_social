"""
LLM Router - Routes requests to the appropriate backend.
Every call is logged to kindness_llm_telemetry for full observability.
"""

import logging
import time
from datetime import datetime, timezone
from utilities.usage_limiter import check_backend_ok, record_usage

logger = logging.getLogger(__name__)

# Fallback order (cheapest/freest first)
FALLBACK_ORDER = ['gemini', 'groq', 'cerebras', 'mistral', 'together', 'deepseek', 'openrouter', 'gpt4o_mini', 'grok', 'haiku', 'local', 'sonnet', 'gpt4o', 'opus']

# Backend to module mapping
_BACKEND_MODULES = {}

# Telemetry context (set by caller to tag calls with agent/thread info)
_telemetry_context = {}


def set_telemetry_context(agent_id=None, thread_id=None, call_type=None):
    """Set context for telemetry logging. Called by evaluator/simulator."""
    global _telemetry_context
    _telemetry_context = {
        'agent_id': agent_id,
        'thread_id': thread_id,
        'call_type': call_type or 'unknown',
    }


def _log_telemetry(backend, actual_backend, messages, max_tokens, temperature,
                   result_text, duration_ms, success, error_message=None, fallback_used=False):
    """Log every LLM call to the telemetry table."""
    try:
        from utilities.postgres_utils import db_cursor
        from utilities.model_registry import get_model_info

        model_info = get_model_info(actual_backend or backend)
        prompt_text = ' '.join(m.get('content', '') for m in messages) if messages else ''

        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO kindness_llm_telemetry
                    (backend, model_id, provider, call_type,
                     agent_id, thread_id,
                     prompt_length, max_tokens, temperature,
                     response_length, response_preview,
                     request_start, request_end, duration_ms,
                     input_tokens, output_tokens, estimated_cost_usd,
                     success, error_message, fallback_used, actual_backend)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                backend,
                model_info.get('model_id', backend),
                model_info.get('provider', '?'),
                _telemetry_context.get('call_type', 'unknown'),
                _telemetry_context.get('agent_id'),
                _telemetry_context.get('thread_id'),
                len(prompt_text),
                max_tokens,
                temperature,
                len(result_text) if result_text else 0,
                (result_text or '')[:100],
                datetime.now(timezone.utc),  # approximate start
                datetime.now(timezone.utc),
                duration_ms,
                len(prompt_text) // 4,  # rough token estimate
                len(result_text) // 4 if result_text else 0,
                0,  # TODO: calculate from usage_limiter BACKEND_INFO
                success,
                error_message[:200] if error_message else None,
                fallback_used,
                actual_backend or backend,
            ))
    except Exception as e:
        # Never let telemetry failures break the actual LLM call
        logger.debug(f"Telemetry log failed: {e}")


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
    Every call is logged to telemetry.

    Returns: (response_text, actual_backend_used)
    """
    backends_to_try = [backend] + [b for b in FALLBACK_ORDER if b != backend]
    fallback_used = False

    for b in backends_to_try:
        # Check usage limits
        status = check_backend_ok(b)
        if not status.allowed:
            logger.info(f"Backend {b} at limit, trying next...")
            if b != backend:
                fallback_used = True
            continue

        try:
            module = _get_backend_module(b)

            # Local backend: check availability
            if b == 'local':
                if not module.is_available():
                    logger.info("Local LLM not available, trying next...")
                    fallback_used = True
                    continue

            start = time.time()

            # Route to correct backend with model-specific params
            if b == 'grok':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-3-auto')
            elif b == 'grok_fast':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-3-fast')
            elif b == 'grok4':
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model='grok-4')
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

            # Log successful telemetry
            _log_telemetry(
                backend=backend, actual_backend=b, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                result_text=result, duration_ms=elapsed_ms,
                success=True, fallback_used=(b != backend),
            )

            logger.info(f"LLM response from {b} in {elapsed_ms}ms")
            return result, b

        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000) if 'start' in dir() else 0

            # Log failed telemetry
            _log_telemetry(
                backend=backend, actual_backend=b, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                result_text=None, duration_ms=elapsed_ms,
                success=False, error_message=str(e), fallback_used=(b != backend),
            )

            logger.warning(f"Backend {b} failed: {e}, trying next...")
            fallback_used = True
            continue

    # All backends failed
    logger.error("All LLM backends failed!")
    _log_telemetry(
        backend=backend, actual_backend=None, messages=messages,
        max_tokens=max_tokens, temperature=temperature,
        result_text=None, duration_ms=0,
        success=False, error_message="All backends failed",
    )
    return "I appreciate your perspective.", backend


def chat_eval(backend, prompt, system="Return ONLY a number 1-10."):
    """
    Shortcut for evaluation calls (low tokens, low temperature).
    Returns: (response_text, actual_backend_used)
    """
    messages = [{"role": "user", "content": prompt}]
    return chat(backend, messages, max_tokens=10, temperature=0.1, system=system)
