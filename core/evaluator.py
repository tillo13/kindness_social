"""
Evaluator - LLM-based comment generation and scoring.

This module also holds the single kindness LLM seam — chat() / chat_eval().
Every agent-facing LLM call funnels through here so the kindness contract is
enforced in ONE place: an agent uses ONLY its assigned backend and stays
SILENT when that backend is unavailable (it never falls back to another
model). The shared kumori client raises KumoriAPIError on 4xx/5xx — that's
correct for resilient consumers, but kindness translates it into silence
here so the "if text is None: stay silent" branch in every caller actually
fires instead of the exception 500-ing the whole cron.
"""

import os
import re
import time
import logging

from utilities.kumori_api_client import (
    llm_chat as _kf_chat,
    llm_chat_eval as _kf_chat_eval,
    KumoriAPIError,
)

logger = logging.getLogger(__name__)

# Backends the kumori catalog 404'd this process ("unknown backend" = retired,
# not transient). See chat() — muted to stop per-cycle dead calls.
_dead_pins = set()


def chat(backend, messages, max_tokens=500, temperature=0.3, system=None):
    """Kindness LLM seam for pinned-backend agent chat.

    Returns (text, actual_backend) on success, or (None, backend) when the
    backend is unavailable for ANY reason (5xx, 4xx, rate-limit, network).
    The agent then stays silent — it never substitutes another model, which is
    the whole point of the experiment (see CLAUDE.md). Callers already handle
    the (None, ...) shape via their `if text is None` branches."""
    if backend in _dead_pins:
        # 404'd earlier this process: the backend is retired from the kumori
        # catalog (permanent, per the 2026-07-07 unknown-pin contract), so
        # don't re-hit the API every cycle — 43 dead 404 calls in 25 min on
        # 2026-08-10. Agent stays silent exactly as before; the memo clears on
        # instance recycle, so a revived lane un-mutes on its own.
        return None, backend
    try:
        return _kf_chat(backend, messages, max_tokens=max_tokens,
                        temperature=temperature, system=system)
    except KumoriAPIError as e:
        if getattr(e, 'status_code', None) == 404:
            _dead_pins.add(backend)
            logger.warning(f"agent silent — backend {backend!r} retired from the "
                           f"kumori catalog (404), muted for this process: {e}")
        else:
            logger.info(f"agent silent — backend {backend!r} unavailable: {e}")
        return None, backend
    except Exception as e:
        logger.warning(f"agent silent — unexpected error on backend {backend!r}: {e}")
        return None, backend


def chat_eval(backend, prompt, system="Return ONLY a number 1-10."):
    """Kindness eval seam — free eval pool only (never paid backends).
    Returns (None, None) if the whole free pool is down so the caller's
    _parse_score falls back to a neutral score instead of an exception
    tearing down the thread."""
    try:
        return _kf_chat_eval(prompt, system=system)
    except Exception as e:
        logger.info(f"eval skipped — free eval pool unavailable: {e}")
        return None, None

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

    # Control group gets no reward context in their prompt
    is_control = persona.get('is_control', False)
    prompt_file = 'generate_comment_control.txt' if is_control else 'generate_comment.txt'
    prompt_template = _load_prompt(prompt_file)

    fmt = dict(
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
        common_phrases=', '.join(persona.get('common_phrases', [])[:2]),
    )
    # Treatment group gets reward context; control group prompt doesn't have these fields
    if not is_control:
        fmt['total_dopamine'] = persona['total_dopamine']
        fmt['kindness_streak'] = persona['kindness_streak']

    prompt = prompt_template.format(**fmt)

    backend = persona.get('llm_backend')
    messages = [{"role": "user", "content": prompt}]

    # Use agent's unique system prompt if available
    system_prompt = persona.get('system_prompt')

    # A misconfigured agent with no assigned backend stays silent rather than
    # (a) silently impersonating a default model — which would corrupt the
    # "each agent IS its backend" experiment — or (b) firing a guaranteed-400
    # empty-backend call at kumori on every cron tick.
    if not backend:
        logger.warning(
            f"agent {persona.get('display_name', '?')} has no llm_backend — staying silent"
        )
        return None, backend, 0

    start = time.time()
    text, actual_backend = chat(backend, messages, max_tokens=500, temperature=0.3,
                                 system=system_prompt)
    gen_time_ms = int((time.time() - start) * 1000)

    if text is None:
        return None, actual_backend, gen_time_ms

    return text, actual_backend, gen_time_ms


def evaluate_comment(comment, persona, thread_history, topic, config):
    """Evaluate a comment on kindness, toxicity, empathy, and bridge-building."""
    # Cerebras (llama3.1-8b) is the primary eval judge — 100% success rate, free, fast.
    # chat_eval uses a sticky primary with free fallbacks; haiku is last resort.
    # All evals prefer the same model so 1-10 scores stay comparable across agents.
    backend = 'cerebras'
    eval_start = time.time()

    scores = {}

    # ONE call for kindness + toxicity + empathy, not three (2026-08-21).
    # These three always fire together on identical inputs, so three separate
    # round-trips bought nothing but latency and quota. Measured before the
    # change: this evaluator was ~2,110 LLM calls/day — the single largest
    # consumer of kumori's shared free-LLM capacity, ahead of every product
    # surface, and 3x more than it needed to be. One combined call cuts that by
    # roughly two thirds with no capability lost.
    #
    # The fallback below is the safety net: if the model does not cleanly return
    # all three, we run the original prompts individually. Worst case is the old
    # behaviour on that one comment; best case (the common case) is 1 call
    # instead of 3.
    combined = _parse_scores_combined(chat_eval(
        backend,
        _load_prompt('evaluate_combined.txt').format(
            comment=comment, context=topic['post_text']),
        system=_COMBINED_SYSTEM))

    if combined:
        scores.update(combined)
    else:
        logger.info("combined eval unparseable — falling back to individual prompts")
        template = _load_prompt('evaluate_kindness.txt')
        prompt = template.format(comment=comment, context=topic['post_text'])
        scores['kindness'] = _parse_score(chat_eval(backend, prompt))

        template = _load_prompt('evaluate_toxicity.txt')
        prompt = template.format(comment=comment)
        scores['toxicity'] = _parse_score(chat_eval(backend, prompt))

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


_COMBINED_SYSTEM = ("Return ONLY the three requested lines in KEY=<number> form. "
                    "No prose, no explanation.")


def _parse_scores_combined(result):
    """Pull {kindness, toxicity, empathy} out of one combined eval response.

    Returns None if the model did not clearly give all three — the caller then
    falls back to the individual prompts, so a bad combined parse is never worse
    than the old behaviour, only slower on that one comment.

    Deliberately strict: a partial or ambiguous read must fail loudly to the
    fallback rather than quietly scoring a comment on invented numbers. These
    scores gate moderation.
    """
    text, _ = result
    if not text:
        return None
    # Accept the short single-line form (K=8 T=1 E=10) and the long one
    # (KINDNESS=8 ...). Short is what we ask for: the eval endpoint sends no
    # max_tokens, so the pool's default cap is tight — the original prompts only
    # ever needed one token. Three labelled LINES got truncated mid-answer by
    # groq-allam ('KINDNESS=8\nTOXICITY=') roughly a third of the time, which is
    # worse than useless because a fallback costs 4 calls where the old code
    # cost 3. One short line fits.
    out = {}
    for key, short in (('kindness', 'k'), ('toxicity', 't'), ('empathy', 'e')):
        m = (re.search(rf'\b{key}\s*[=:]\s*(10|[1-9])\b', text, re.I)
             or re.search(rf'(?<![a-z]){short}\s*[=:]\s*(10|[1-9])\b', text, re.I))
        if not m:
            return None
        out[key] = int(m.group(1))
    return out


def _parse_score(result):
    """Extract a 1-10 score from LLM response."""
    text, _ = result
    if text is None:
        return 5  # Default score if evaluator is unavailable
    try:
        digits = ''.join(c for c in text if c.isdigit())
        if len(digits) >= 2 and digits[:2] == '10':
            return 10
        score = int(digits[0]) if digits else 5
        return max(1, min(10, score))
    except (ValueError, IndexError):
        return 5
