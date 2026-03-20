"""
Grok LLM Backend - via grok_core ECDSA zero-auth handshake.
Supports grok-3-fast, grok-3-auto, grok-4. All free, no API key needed.
Uses the reverse-engineered cryptographic challenge flow from dr_nick.
"""

import sys
import os
import logging

logger = logging.getLogger(__name__)

# Add dr_nick to path so we can import grok_core
DR_NICK_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'dr_nick')
if os.path.exists(DR_NICK_PATH):
    sys.path.insert(0, os.path.abspath(DR_NICK_PATH))

# Also check a bundled copy
BUNDLED_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'grok_core')
if os.path.exists(BUNDLED_PATH):
    sys.path.insert(0, os.path.abspath(os.path.join(BUNDLED_PATH, '..')))

_instances = {}


def _get_grok(model='grok-3-fast'):
    """Get or create a Grok instance for a specific model."""
    if model not in _instances:
        try:
            from grok_core.grok import Grok
            _instances[model] = Grok(model=model)
            logger.info(f"Grok initialized for {model}")
        except ImportError as e:
            raise RuntimeError(
                f"grok_core not available: {e}. "
                f"Ensure dr_nick is at {DR_NICK_PATH} or grok_core is bundled."
            )
    return _instances[model]


def chat(messages, max_tokens=500, temperature=0.3, system=None, model='grok-3-fast'):
    """
    Generate text via Grok zero-auth ECDSA flow.
    models: grok-3-fast, grok-3-auto, grok-4
    """
    # Combine messages into a single prompt
    parts = []
    if system:
        parts.append(f"System: {system}")
    for msg in messages:
        if msg['role'] == 'system':
            parts.append(f"System: {msg['content']}")
        else:
            parts.append(msg['content'])
    prompt = "\n\n".join(parts)

    try:
        g = _get_grok(model)
        result = g.start_convo(prompt)
        text = result.get('response', '')
        if text:
            return text.strip()
        raise RuntimeError("Empty response from Grok")
    except Exception as e:
        logger.error(f"Grok ({model}) error: {e}")
        raise
