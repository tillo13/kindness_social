"""
LLM Router - Routes requests to the appropriate backend.
Every call is logged to kindness_llm_telemetry for full observability.
"""

import logging
import time
from datetime import datetime, timezone
from utilities.usage_limiter import check_backend_ok, record_usage, mark_backend_backoff, clear_backend_backoff

logger = logging.getLogger(__name__)

# Fallback order (cheapest/freest first)
# Fallback order — only backends that work reliably on App Engine
# grok/deepseek need native deps (Cloud Run / local only, not App Engine)
# gemini has tight per-minute quota, put it later
# openrouter free models are flaky, put it last
FALLBACK_ORDER = ['groq', 'groq-kimi', 'groq-qwen', 'groq-gptoss', 'cerebras', 'mistral', 'llm7', 'nvidia', 'gpt4o_mini', 'haiku', 'sonnet', 'gemini', 'openrouter', 'gpt4o', 'opus']

# These only work on Cloud Run / locally (need native deps that App Engine can't install)
CLOUD_RUN_ONLY = {'grok', 'grok_fast', 'grok4', 'deepseek'}

CLOUD_RUN_WORKER_URL = 'https://kindness-worker-243380010344.us-central1.run.app'


def _proxy_to_worker(backend, messages, max_tokens=500, temperature=0.3, system=None):
    """Proxy a chat request to the Cloud Run worker for backends that can't run on App Engine."""
    import requests as http_req
    start = time.time()
    try:
        payload = {
            'backend': backend,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': temperature,
        }
        if system:
            payload['system'] = system

        resp = http_req.post(
            f'{CLOUD_RUN_WORKER_URL}/chat',
            json=payload,
            timeout=120,
        )
        elapsed = int((time.time() - start) * 1000)

        if resp.ok:
            data = resp.json()
            text = data.get('text', '')
            actual = data.get('backend', backend)
            if text:
                logger.info(f"Cloud Run proxy OK for {backend} ({elapsed}ms)")
                _log_telemetry(backend, actual, messages, max_tokens, temperature,
                              text, elapsed, True)
                record_usage(backend)
                return text, actual

        logger.warning(f"Cloud Run proxy returned {resp.status_code} for {backend}")
        return None
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.warning(f"Cloud Run proxy failed for {backend}: {e} ({elapsed}ms)")
        _log_telemetry(backend, backend, messages, max_tokens, temperature,
                      '', elapsed, False, error_message=str(e)[:200])
        return None


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
        elif backend in ('groq', 'groq-kimi', 'groq-qwen', 'groq-gptoss'):
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
        elif backend == 'nvidia':
            from utilities.llm_backends import nvidia
            _BACKEND_MODULES[backend] = nvidia
        elif backend == 'llm7':
            from utilities.llm_backends import llm7
            _BACKEND_MODULES[backend] = llm7
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
    # Block Cloud Run-only backends on App Engine — don't even attempt them
    import os
    is_appengine = os.environ.get('GAE_ENV', '').startswith('standard')
    is_cloud_run_only = backend in CLOUD_RUN_ONLY

    if is_appengine and is_cloud_run_only:
        # Proxy to Cloud Run worker — no fallback, this agent uses this backend
        try:
            result = _proxy_to_worker(backend, messages, max_tokens, temperature, system)
            if result:
                return result
        except Exception as e:
            logger.warning(f"Cloud Run proxy failed for {backend}: {e}")
            mark_backend_backoff(backend, 300)
            return None, backend

    # No fallback chain — each agent uses ONLY its assigned backend.
    # If it fails, the agent stays silent. That's honest.
    backends_to_try = [backend]
    if is_appengine:
        backends_to_try = [b for b in backends_to_try if b not in CLOUD_RUN_ONLY]

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
            # (result validated after the call below)
            if b in ('groq-kimi', 'groq-qwen', 'groq-gptoss'):
                from utilities.llm_backends.groq import GROQ_MODELS
                result = module.chat(messages, max_tokens, temperature,
                                     system=system, model=GROQ_MODELS[b])
            elif b == 'grok':
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
                                     system=system, tier=b,
                                     feature=_telemetry_context.get('call_type', 'chat'))
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

            # Validate response — empty/None means backend returned garbage
            if not result or not result.strip():
                _log_telemetry(
                    backend=backend, actual_backend=b, messages=messages,
                    max_tokens=max_tokens, temperature=temperature,
                    result_text=None, duration_ms=elapsed_ms,
                    success=False, error_message="Empty response from backend",
                    fallback_used=(b != backend),
                )
                # Back off flaky backends that return empties (openrouter, etc)
                mark_backend_backoff(b, 300)  # 5 min cooldown for empty responses
                logger.warning(f"Backend {b} returned empty response, backing off 300s")
                fallback_used = True
                continue

            record_usage(b)
            clear_backend_backoff(b)  # Reset exponential backoff on success

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
            error_str = str(e)

            # Log failed telemetry
            _log_telemetry(
                backend=backend, actual_backend=b, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                result_text=None, duration_ms=elapsed_ms,
                success=False, error_message=error_str, fallback_used=(b != backend),
            )

            # Auto-backoff on rate limit errors
            if '429' in error_str or 'rate limit' in error_str.lower() or 'quota' in error_str.lower():
                # Backend-specific base backoff (exponential multiplier applied in mark_backend_backoff)
                if b == 'gemini':
                    backoff_secs = 300  # 5 min base — Gemini free tier has strict per-minute quota
                elif b == 'groq':
                    backoff_secs = 180  # 3 min base — Groq rate limits frequently on free tier
                elif b == 'openrouter':
                    backoff_secs = 300  # 5 min base — OpenRouter free models are flaky
                else:
                    backoff_secs = 120  # 2 min base
                # Override with retry-after header if available
                if 'retry in' in error_str.lower():
                    import re
                    match = re.search(r'retry in (\d+)', error_str.lower())
                    if match:
                        backoff_secs = min(int(match.group(1)) + 10, 900)  # cap at 15 min
                mark_backend_backoff(b, backoff_secs)

            logger.warning(f"Backend {b} failed: {error_str[:80]}")
            # No fallback — return None so the caller knows this agent can't speak right now
            return None, backend

    # Backend was blocked (in backoff or at limit)
    logger.info(f"Backend {backend} unavailable — agent stays silent")
    return None, backend



# Eval fallback chain — Groq primary for consistency, then other free backends,
# paid models dead last.  Sticky primary (no round-robin) so the same judge
# scores every comment and 1-10 ratings stay comparable.
EVAL_BACKENDS = [
    'cerebras',      # primary — 100% success rate, fast, free, consistent
    'mistral',       # free fallback (99.7% success)
    'groq',          # free fallback (fast but flaky ~59%)
    'groq-kimi',     # free fallback — kimi-k2 on Groq, 1K RPD
    'groq-qwen',     # free fallback — qwen3-32b on Groq, 1K RPD
    'llm7',          # free fallback — no key needed
    'together',      # free fallback
    'nvidia',        # free but lifetime credits — conserve
    'gemini',        # free fallback (250/day, low success)
    'gpt4o_mini',    # cheap paid fallback
    'haiku',         # absolute last resort
]


def chat_eval(backend, prompt, system="Return ONLY a number 1-10."):
    """
    Shortcut for evaluation calls (low tokens, low temperature).
    Evaluation is a SYSTEM function (scoring), not an agent voice —
    so it uses a sticky free-tier primary (Groq) for consistency,
    with free fallbacks before any paid model.
    Returns: (response_text, actual_backend_used)
    """
    messages = [{"role": "user", "content": prompt}]

    for b in EVAL_BACKENDS:
        status = check_backend_ok(b)
        if not status.allowed:
            continue

        start = time.time()
        try:
            module = _get_backend_module(b)
            if b in ('haiku', 'sonnet', 'opus'):
                result = module.chat(messages, 10, 0.1, system=system, tier=b)
            elif b == 'gpt4o_mini':
                result = module.chat(messages, 10, 0.1, system=system, model='gpt-4o-mini')
            elif b in ('groq-kimi', 'groq-qwen', 'groq-gptoss'):
                from utilities.llm_backends.groq import GROQ_MODELS
                result = module.chat(messages, 10, 0.1, system=system, model=GROQ_MODELS[b])
            else:
                result = module.chat(messages, 10, 0.1, system=system)

            elapsed_ms = int((time.time() - start) * 1000)

            if result and result.strip():
                record_usage(b)
                clear_backend_backoff(b)
                _log_telemetry(
                    backend='eval', actual_backend=b, messages=messages,
                    max_tokens=10, temperature=0.1,
                    result_text=result, duration_ms=elapsed_ms,
                    success=True, fallback_used=(b != 'cerebras'),
                )
                return result, b
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.debug(f"Eval backend {b} failed: {e}")
            mark_backend_backoff(b, 120)
            continue

    return None, 'eval'
