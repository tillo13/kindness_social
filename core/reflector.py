"""
Agent Reflector — Each agent's "brain" that reviews its own performance,
observes what's working for others, and decides whether to adjust.

No more mechanical personality drift. Agents genuinely reason about
their situation using their own LLM backend, and their personality traits
(defensiveness, openness, stubbornness) determine how much they change.

Runs as a cron job every 2-4 hours on batches of agents.
"""

import json
import logging
import os
import time

from core import db_ops
from utilities.llm_router import chat, set_telemetry_context
from utilities.postgres_utils import db_cursor

logger = logging.getLogger(__name__)

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'prompts')


def _load_prompt():
    with open(os.path.join(PROMPTS_DIR, 'reflect.txt'), 'r') as f:
        return f.read()


def get_agents_due_for_reflection(batch_size=10):
    """Get agents who've had enough new interactions to reflect.
    Prioritizes agents with more interactions since last reflection."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM kindness_agents
            WHERE is_active = TRUE
              AND is_control = FALSE
              AND total_interactions >= 5
              AND (total_interactions - COALESCE(interactions_at_last_reflection, 0)) >= 3
            ORDER BY
                (total_interactions - COALESCE(interactions_at_last_reflection, 0)) DESC,
                last_reflected_at ASC NULLS FIRST
            LIMIT %s
        """, (batch_size,))
        return [dict(row) for row in cur.fetchall()]


def get_agent_recent_comments(agent_db_id, limit=8):
    """Get agent's recent comments with scores for self-review."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT comment_text, kindness_score, toxicity_score,
                   empathy_score, bridge_score, dopamine_earned, dopamine_source
            FROM kindness_comments
            WHERE agent_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (agent_db_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_platform_context():
    """Get what's working on the platform — so agents can see the leaderboard."""
    with db_cursor(dict_cursor=True) as cur:
        # Top earner
        cur.execute("""
            SELECT MAX(total_dopamine) as top_dopamine
            FROM kindness_agents WHERE is_active = TRUE AND total_interactions > 0
        """)
        top = cur.fetchone()['top_dopamine'] or 0

        # Platform avg kindness
        cur.execute("SELECT AVG(kindness_score) as avg_k FROM kindness_comments")
        avg_k = round(float(cur.fetchone()['avg_k'] or 5), 1)

        # Avg dopamine per comment for kind vs toxic
        cur.execute("""
            SELECT
                AVG(CASE WHEN kindness_score >= 7 THEN dopamine_earned END) as kind_avg,
                AVG(CASE WHEN toxicity_score >= 7 THEN dopamine_earned END) as toxic_avg
            FROM kindness_comments
        """)
        row = cur.fetchone()
        kind_avg = round(float(row['kind_avg'] or 0), 1)
        toxic_avg = round(float(row['toxic_avg'] or 0), 1)

        return {
            'top_earner_dopamine': top,
            'avg_kindness': avg_k,
            'kind_avg_dopamine': kind_avg,
            'toxic_avg_dopamine': toxic_avg,
        }


def reflect_agent(agent, platform_ctx):
    """Run a single agent's reflection. Returns the reflection result."""
    from utilities.usage_limiter import is_backend_in_backoff
    backend = agent.get('llm_backend', 'gemini')
    if is_backend_in_backoff(backend):
        return None

    # Get their recent comments
    recent = get_agent_recent_comments(agent['id'])
    if not recent:
        return None

    # Format recent comments for the prompt
    comment_lines = []
    for c in recent:
        comment_lines.append(
            f"  - \"{c['comment_text'][:100]}\" "
            f"(kindness: {c['kindness_score']}, toxicity: {c['toxicity_score']}, "
            f"earned: {c['dopamine_earned']} dp from {c['dopamine_source'] or 'none'})"
        )

    my_avg_k = sum(c['kindness_score'] or 0 for c in recent) / len(recent)

    prompt_template = _load_prompt()
    prompt = prompt_template.format(
        persona_name=agent['display_name'],
        current_toxicity=round(agent['current_toxicity'], 1),
        current_empathy=round(agent['current_empathy'], 1),
        defensiveness=agent.get('defensiveness', 5.0),
        agreeableness=agent.get('agreeableness', 5.0),
        openness_to_change=round(agent['openness_to_change'], 2),
        total_dopamine=agent['total_dopamine'],
        kindness_streak=agent['kindness_streak'],
        total_interactions=agent['total_interactions'],
        recent_comments='\n'.join(comment_lines),
        top_earner_dopamine=platform_ctx['top_earner_dopamine'],
        avg_kindness=platform_ctx['avg_kindness'],
        my_avg_kindness=round(my_avg_k, 1),
        kind_avg_dopamine=platform_ctx['kind_avg_dopamine'],
        toxic_avg_dopamine=platform_ctx['toxic_avg_dopamine'],
    )

    set_telemetry_context(agent_id=agent.get('agent_id'), call_type='reflect')

    try:
        response, actual_backend = chat(
            backend,
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.4,
        )

        # Parse JSON response
        # Strip markdown backticks if present
        text = response.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)

        # Validate and clamp adjustments
        will_adjust = bool(result.get('will_adjust', False))
        tox_adj = float(result.get('toxicity_adjustment', 0))
        emp_adj = float(result.get('empathy_adjustment', 0))

        # Clamp to allowed ranges
        tox_adj = max(-0.3, min(0.0, tox_adj))
        emp_adj = max(0.0, min(0.3, emp_adj))

        # Personality gates: defensive/stubborn agents change LESS
        # even if the LLM said they would (the LLM might be too optimistic)
        defensiveness = agent.get('defensiveness', 5.0)
        openness = agent.get('openness_to_change', 0.5)

        # Scale adjustments by personality: high defensiveness dampens change
        personality_factor = openness * (1 - (defensiveness / 15))
        personality_factor = max(0.1, min(1.0, personality_factor))

        tox_adj *= personality_factor
        emp_adj *= personality_factor

        # Floor tiny changes
        if abs(tox_adj) < 0.005:
            tox_adj = 0
        if abs(emp_adj) < 0.005:
            emp_adj = 0

        old_tox = agent['current_toxicity']
        old_emp = agent['current_empathy']
        new_tox = max(1.0, old_tox + tox_adj) if will_adjust else old_tox
        new_emp = min(10.0, old_emp + emp_adj) if will_adjust else old_emp

        # Save reflection to history
        interactions_since = agent['total_interactions'] - (agent.get('interactions_at_last_reflection') or 0)
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO kindness_reflections
                    (agent_id, reflection_text, decided_to_change, change_reason,
                     old_toxicity, new_toxicity, old_empathy, new_empathy,
                     interactions_since_last)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                agent['id'],
                result.get('internal_thought', ''),
                will_adjust,
                result.get('reason', ''),
                old_tox, new_tox, old_emp, new_emp,
                interactions_since,
            ))

        # Apply changes to agent
        if will_adjust and (tox_adj != 0 or emp_adj != 0):
            with db_cursor() as cur:
                cur.execute("""
                    UPDATE kindness_agents SET
                        current_toxicity = %s,
                        current_empathy = %s,
                        last_reflected_at = NOW(),
                        interactions_at_last_reflection = total_interactions,
                        updated_at = NOW()
                    WHERE id = %s
                """, (new_tox, new_emp, agent['id']))
        else:
            # Still mark that reflection happened
            with db_cursor() as cur:
                cur.execute("""
                    UPDATE kindness_agents SET
                        last_reflected_at = NOW(),
                        interactions_at_last_reflection = total_interactions
                    WHERE id = %s
                """, (agent['id'],))

        # Slowly increase openness after each reflection (even if no change)
        # Reflecting itself makes you slightly more open over time
        new_openness = min(1.0, openness + 0.005)
        with db_cursor() as cur:
            cur.execute("""
                UPDATE kindness_agents SET openness_to_change = %s WHERE id = %s
            """, (new_openness, agent['id']))

        logger.info(
            f"  {agent['display_name']} reflected: "
            f"{'CHANGED' if will_adjust else 'no change'} "
            f"tox {old_tox:.1f}->{new_tox:.1f} emp {old_emp:.1f}->{new_emp:.1f} "
            f"reason: {result.get('reason', '?')[:60]}"
        )

        return {
            'agent': agent['display_name'],
            'changed': will_adjust,
            'thought': result.get('internal_thought', ''),
            'reason': result.get('reason', ''),
            'tox_change': round(new_tox - old_tox, 3),
            'emp_change': round(new_emp - old_emp, 3),
        }

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"  {agent['display_name']} reflection parse error: {e}")
        # Still mark reflected so we don't retry immediately
        with db_cursor() as cur:
            cur.execute("""
                UPDATE kindness_agents SET
                    last_reflected_at = NOW(),
                    interactions_at_last_reflection = total_interactions
                WHERE id = %s
            """, (agent['id'],))
        return None
    except Exception as e:
        logger.error(f"  {agent['display_name']} reflection failed: {e}")
        return None


def run_reflection_cycle(batch_size=10):
    """Main entry point — called by cron. Reflects a batch of agents."""
    agents = get_agents_due_for_reflection(batch_size)
    if not agents:
        logger.info("No agents due for reflection")
        return {'reflected': 0, 'changed': 0, 'results': []}

    platform_ctx = get_platform_context()
    results = []
    changed = 0

    for agent in agents:
        result = reflect_agent(agent, platform_ctx)
        if result:
            results.append(result)
            if result['changed']:
                changed += 1

    logger.info(f"Reflection cycle: {len(results)} reflected, {changed} changed")
    return {
        'reflected': len(results),
        'changed': changed,
        'results': results,
    }


def get_agent_reflections(agent_db_id, limit=10):
    """Get an agent's reflection history for their profile page."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM kindness_reflections
            WHERE agent_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (agent_db_id, limit))
        return [dict(r) for r in cur.fetchall()]
