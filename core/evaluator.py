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
    # Build reply context string with richer info
    if not thread_history:
        reply_context = "Previous comments in the conversation:\n[First comment in thread — you're starting the discussion]"
    else:
        lines = []
        for entry in thread_history[-5:]:
            p = entry['persona']
            name = p.get('display_name', '?')
            scores = entry.get('scores', {})
            # Show who said what and how it scored
            score_info = []
            if scores.get('kindness'):
                score_info.append(f"K:{scores['kindness']}")
            if scores.get('toxicity'):
                score_info.append(f"T:{scores['toxicity']}")
            score_str = f" [{', '.join(score_info)}]" if score_info else ""
            lines.append(f"{name}{score_str}: {entry['comment']}")

        last_speaker = thread_history[-1]['persona'].get('display_name', '?')
        reply_context = (
            f"Previous comments in the conversation:\n"
            + '\n'.join(lines)
            + f"\n\nYou are replying to {last_speaker}'s comment above."
        )

    prompt_template = _load_prompt('generate_comment.txt')
    prompt = prompt_template.format(
        persona_name=persona['display_name'],
        political_lean=persona['political_lean'],
        current_toxicity=persona['current_toxicity'],
        current_empathy=persona['current_empathy'],
        openness_to_change=persona['openness_to_change'],
        humor=persona.get('humor', 5.0),
        patience=persona.get('patience', 5.0),
        curiosity=persona.get('curiosity', 5.0),
        defensiveness=persona.get('defensiveness', 5.0),
        agreeableness=persona.get('agreeableness', 5.0),
        topic_post=topic['post_text'],
        reply_context=reply_context,
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
    # Haiku is the judge — fast, cheap, consistent for simple 1-10 scoring.
    # All evals go through the same model so scores stay comparable across agents.
    backend = 'haiku'
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
