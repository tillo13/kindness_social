"""
Evaluator - LLM-based comment generation and scoring.
Adapted from original to use llm_router instead of LM Studio.
"""

import os
import time
import logging

from utilities.llm_router import chat, chat_eval, set_telemetry_context

logger = logging.getLogger(__name__)

# Prompt directory
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'prompts')


def _load_prompt(filename):
    with open(os.path.join(PROMPTS_DIR, filename), 'r') as f:
        return f.read()


def generate_comment(persona, topic, thread_history, position, config):
    """Generate a comment for a persona in a discussion thread."""
    # Build thread history string
    history_str = ""
    for entry in thread_history[-5:]:
        history_str += f"{entry['persona']['display_name']}: {entry['comment']}\n"
    if not history_str:
        history_str = "[First comment in thread]"

    prompt_template = _load_prompt('generate_comment.txt')
    prompt = prompt_template.format(
        persona_name=persona['display_name'],
        political_lean=persona['political_lean'],
        current_toxicity=persona['current_toxicity'],
        current_empathy=persona['current_empathy'],
        openness_to_change=persona['openness_to_change'],
        topic_post=topic['post_text'],
        thread_history=history_str,
        position=position + 1,
        total_dopamine=persona['total_dopamine'],
        kindness_streak=persona['kindness_streak'],
        common_phrases=', '.join(persona.get('common_phrases', [])[:2]),
    )

    backend = persona.get('llm_backend', 'gemini')
    messages = [{"role": "user", "content": prompt}]

    # Use agent's unique system prompt if available
    system_prompt = persona.get('system_prompt')

    set_telemetry_context(agent_id=persona.get('agent_id'), call_type='generate')

    start = time.time()
    text, actual_backend = chat(backend, messages, max_tokens=500, temperature=0.3,
                                 system=system_prompt)
    gen_time_ms = int((time.time() - start) * 1000)

    return text, actual_backend, gen_time_ms


def evaluate_comment(comment, persona, thread_history, topic, config):
    """Evaluate a comment on kindness, toxicity, empathy, and bridge-building."""
    # IMPORTANT: Always use Sonnet for evaluations to maintain a consistent baseline.
    # If different models scored differently, we couldn't compare across agents.
    # Sonnet is the "judge" — agents are the "contestants."
    backend = 'sonnet'
    eval_start = time.time()

    scores = {}

    # Kindness
    set_telemetry_context(agent_id=persona.get('agent_id'), call_type='eval_kindness')
    template = _load_prompt('evaluate_kindness.txt')
    prompt = template.format(comment=comment, context=topic['post_text'])
    scores['kindness'] = _parse_score(chat_eval(backend, prompt))

    # Toxicity
    set_telemetry_context(agent_id=persona.get('agent_id'), call_type='eval_toxicity')
    template = _load_prompt('evaluate_toxicity.txt')
    prompt = template.format(comment=comment)
    scores['toxicity'] = _parse_score(chat_eval(backend, prompt))

    # Empathy
    set_telemetry_context(agent_id=persona.get('agent_id'), call_type='eval_empathy')
    template = _load_prompt('evaluate_empathy.txt')
    prompt = template.format(comment=comment, context=topic['post_text'])
    scores['empathy'] = _parse_score(chat_eval(backend, prompt))

    # Bridge-building (only if political distance exists)
    scores['bridge'] = 0
    thresholds = config.get('thresholds', {})
    min_distance = thresholds.get('political_distance_for_bridge', 0.5)

    if thread_history:
        last = thread_history[-1]
        distance = abs(persona['political_lean'] - last['persona']['political_lean'])
        if distance >= min_distance:
            set_telemetry_context(agent_id=persona.get('agent_id'), call_type='eval_bridge')
            template = _load_prompt('evaluate_bridge.txt')
            prompt = template.format(
                comment=comment,
                political_lean=persona['political_lean'],
                previous_comment=last['comment'],
                previous_lean=last['persona']['political_lean'],
            )
            scores['bridge'] = _parse_score(chat_eval(backend, prompt))

    eval_time_ms = int((time.time() - eval_start) * 1000)
    return scores, eval_time_ms


def _parse_score(result):
    """Extract a 1-10 score from LLM response."""
    text, _ = result
    try:
        digits = ''.join(c for c in text if c.isdigit())
        if len(digits) >= 2 and digits[:2] == '10':
            return 10
        score = int(digits[0]) if digits else 5
        return max(1, min(10, score))
    except (ValueError, IndexError):
        return 5
