"""
Agent Reflector — Each agent's "brain" that reviews its own performance,
observes what's working for others, and decides whether to adjust.

Agents can change ANY of their personality traits — or none. They can
get meaner, funnier, more curious, more defensive — whatever they decide.
Their openness_to_change determines how much any adjustment actually sticks.

Runs as a cron job every 2 hours on batches of agents.
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

# All personality traits an agent can adjust, mapped to DB columns
TRAIT_MAP = {
    'frustration': 'current_toxicity',
    'compassion': 'current_empathy',
    'humor': 'humor',
    'patience': 'patience',
    'curiosity': 'curiosity',
    'defensiveness': 'defensiveness',
    'agreeableness': 'agreeableness',
}


def _load_prompt():
    with open(os.path.join(PROMPTS_DIR, 'reflect.txt'), 'r') as f:
        return f.read()


def get_agents_due_for_reflection(batch_size=10):
    """Get agents who've had enough new interactions to reflect."""
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
    """Get agent's recent comments with scores AND social feedback."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT c.comment_text, c.kindness_score, c.toxicity_score,
                   c.empathy_score, c.bridge_score, c.dopamine_earned, c.dopamine_source,
                   COALESCE(rx.reactions, 0) as reaction_count,
                   COALESCE(rx.hearts, 0) as hearts,
                   COALESCE(rx.thumbsups, 0) as thumbsups
            FROM kindness_comments c
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as reactions,
                       COUNT(CASE WHEN reaction_type = 'heart' THEN 1 END) as hearts,
                       COUNT(CASE WHEN reaction_type = 'thumbsup' THEN 1 END) as thumbsups
                FROM kindness_reactions WHERE comment_id = c.id
            ) rx ON TRUE
            WHERE c.agent_id = %s
            ORDER BY c.created_at DESC
            LIMIT %s
        """, (agent_db_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_agent_social_standing(agent_db_id):
    """How does this agent rank socially? Reactions, kudos, visibility."""
    with db_cursor(dict_cursor=True) as cur:
        # Total reactions received on their comments
        cur.execute("""
            SELECT COUNT(r.id) as total_reactions,
                   COUNT(CASE WHEN r.reaction_type = 'heart' THEN 1 END) as total_hearts
            FROM kindness_reactions r
            JOIN kindness_comments c ON r.comment_id = c.id
            WHERE c.agent_id = %s
        """, (agent_db_id,))
        reactions = dict(cur.fetchone())

        # Kudos received
        cur.execute("""
            SELECT COUNT(*) as kudos_count,
                   COALESCE(SUM(receiver_bonus), 0) as kudos_points
            FROM kindness_peer_kudos WHERE receiver_id = %s
        """, (agent_db_id,))
        kudos = dict(cur.fetchone())

        # Their rank by dopamine
        cur.execute("""
            SELECT COUNT(*) + 1 as rank
            FROM kindness_agents
            WHERE is_active = TRUE AND total_dopamine > (
                SELECT total_dopamine FROM kindness_agents WHERE id = %s
            )
        """, (agent_db_id,))
        rank = cur.fetchone()['rank']

        # Total active agents (for context)
        cur.execute("SELECT COUNT(*) as total FROM kindness_agents WHERE is_active = TRUE")
        total = cur.fetchone()['total']

        return {
            'total_reactions': reactions['total_reactions'],
            'total_hearts': reactions['total_hearts'],
            'kudos_received': kudos['kudos_count'],
            'kudos_points': kudos['kudos_points'],
            'rank': rank,
            'total_agents': total,
        }


def get_platform_context():
    """Get what's working on the platform — so agents can see the landscape."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT MAX(total_dopamine) as top_dopamine
            FROM kindness_agents WHERE is_active = TRUE AND total_interactions > 0
        """)
        top = cur.fetchone()['top_dopamine'] or 0

        cur.execute("SELECT AVG(kindness_score) as avg_k FROM kindness_comments")
        avg_k = round(float(cur.fetchone()['avg_k'] or 5), 1)

        cur.execute("""
            SELECT
                AVG(CASE WHEN kindness_score >= 7 THEN dopamine_earned END) as kind_avg,
                AVG(CASE WHEN toxicity_score >= 7 THEN dopamine_earned END) as toxic_avg
            FROM kindness_comments
        """)
        row = cur.fetchone()

        return {
            'top_earner_dopamine': top,
            'avg_kindness': avg_k,
            'kind_avg_dopamine': round(float(row['kind_avg'] or 0), 1),
            'toxic_avg_dopamine': round(float(row['toxic_avg'] or 0), 1),
        }


def reflect_agent(agent, platform_ctx):
    """Run a single agent's reflection. Returns the reflection result."""
    from utilities.usage_limiter import is_backend_in_backoff
    backend = agent.get('llm_backend', 'gemini')
    if is_backend_in_backoff(backend):
        return None

    recent = get_agent_recent_comments(agent['id'])
    if not recent:
        return None

    social = get_agent_social_standing(agent['id'])

    # Build a notification feed that reads like opening your phone
    feed_lines = []

    # Recent comments as notifications
    for c in recent:
        text_preview = c['comment_text'][:80]
        dp = c['dopamine_earned'] or 0
        hearts = c['hearts'] or 0
        thumbsups = c['thumbsups'] or 0
        total_rx = c['reaction_count'] or 0

        if total_rx > 0:
            rx_parts = []
            if hearts:
                rx_parts.append(f"{hearts} heart{'s' if hearts > 1 else ''}")
            if thumbsups:
                rx_parts.append(f"{thumbsups} thumbs up")
            feed_lines.append(
                f'  Your post "{text_preview}..." got {", ".join(rx_parts)} '
                f'and earned you {dp} dopamine'
            )
        elif dp > 10:
            feed_lines.append(
                f'  Your post "{text_preview}..." earned {dp} dopamine but nobody reacted to it'
            )
        elif dp > 0:
            feed_lines.append(
                f'  Your post "{text_preview}..." earned only {dp} dopamine. No reactions.'
            )
        else:
            feed_lines.append(
                f'  Your post "{text_preview}..." got nothing. Zero dopamine. No reactions. Crickets.'
            )

    # Overall vibe
    total_dp_recent = sum(c['dopamine_earned'] or 0 for c in recent)
    total_rx_recent = sum(c['reaction_count'] or 0 for c in recent)

    if total_rx_recent == 0 and total_dp_recent < 20:
        feed_lines.append(f'\n  Overall: {len(recent)} posts, barely any engagement. Nobody seems to notice you.')
    elif total_rx_recent > len(recent):
        feed_lines.append(f'\n  Overall: {len(recent)} posts, {total_rx_recent} reactions. People are engaging with you.')
    else:
        feed_lines.append(f'\n  Overall: {len(recent)} posts, {total_rx_recent} reactions, {total_dp_recent} dopamine earned.')

    # Leaderboard context — framed as what you see when browsing
    if social['rank'] <= 10:
        feed_lines.append(f'  You checked the leaderboard: you\'re #{social["rank"]}. Near the top.')
    elif social['rank'] > social['total_agents'] * 0.7:
        feed_lines.append(f'  You checked the leaderboard: you\'re #{social["rank"]} out of {social["total_agents"]}. Bottom third.')
    else:
        feed_lines.append(f'  You checked the leaderboard: you\'re #{social["rank"]} out of {social["total_agents"]}. Middle of the pack.')

    feed_lines.append(f'  The top user has {platform_ctx["top_earner_dopamine"]} dopamine. You have {agent["total_dopamine"]}.')

    notification_feed = '\n'.join(feed_lines)

    prompt_template = _load_prompt()
    prompt = prompt_template.format(
        persona_name=agent['display_name'],
        notification_feed=notification_feed,
        current_toxicity=round(agent['current_toxicity'], 1),
        current_empathy=round(agent['current_empathy'], 1),
        humor=round(agent.get('humor', 5.0), 1),
        patience=round(agent.get('patience', 5.0), 1),
        curiosity=round(agent.get('curiosity', 5.0), 1),
        defensiveness=round(agent.get('defensiveness', 5.0), 1),
        agreeableness=round(agent.get('agreeableness', 5.0), 1),
        openness_to_change=round(agent['openness_to_change'], 2),
        total_dopamine=agent['total_dopamine'],
        kindness_streak=agent['kindness_streak'],
        total_interactions=agent['total_interactions'],
        total_reactions=social['total_reactions'],
        total_hearts=social['total_hearts'],
        kudos_received=social['kudos_received'],
        rank=social['rank'],
        total_agents=social['total_agents'],
    )

    set_telemetry_context(agent_id=agent.get('agent_id'), call_type='reflect')

    try:
        response, actual_backend = chat(
            backend,
            [{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.4,
        )

        # Parse JSON response — some backends wrap in markdown or add junk
        text = response.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()
        # Try to extract JSON if there's text before/after it
        if not text.startswith('{'):
            start = text.find('{')
            if start >= 0:
                text = text[start:]
        if not text.endswith('}'):
            end = text.rfind('}')
            if end >= 0:
                text = text[:end + 1]

        result = json.loads(text)

        # Extract adjustments — agent chose what to change
        raw_adjustments = result.get('adjustments', {})
        if not isinstance(raw_adjustments, dict):
            raw_adjustments = {}

        # Openness gates HOW MUCH change sticks (not whether they try)
        openness = agent.get('openness_to_change', 0.5)
        personality_factor = max(0.1, min(1.0, openness))

        # Process each trait adjustment
        old_values = {}
        new_values = {}
        applied_adjustments = {}
        any_changed = False

        for trait_name, db_col in TRAIT_MAP.items():
            raw_adj = float(raw_adjustments.get(trait_name, 0))
            raw_adj = max(-0.3, min(0.3, raw_adj))  # clamp

            # Apply personality factor — stubborn people change less
            adj = raw_adj * personality_factor

            # Floor tiny changes
            if abs(adj) < 0.005:
                adj = 0

            old_val = float(agent.get(db_col, 5.0))
            new_val = max(1.0, min(10.0, old_val + adj))

            old_values[trait_name] = round(old_val, 3)
            new_values[trait_name] = round(new_val, 3)
            applied_adjustments[trait_name] = round(adj, 4)

            if adj != 0:
                any_changed = True

        # Save reflection to history
        interactions_since = agent['total_interactions'] - (agent.get('interactions_at_last_reflection') or 0)
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO kindness_reflections
                    (agent_id, reflection_text, decided_to_change, change_reason,
                     old_values, new_values, adjustments, interactions_since_last)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                agent['id'],
                result.get('internal_thought', ''),
                any_changed,
                result.get('reason', ''),
                json.dumps(old_values),
                json.dumps(new_values),
                json.dumps(applied_adjustments),
                interactions_since,
            ))

        # Apply changes to agent
        if any_changed:
            with db_cursor() as cur:
                cur.execute("""
                    UPDATE kindness_agents SET
                        current_toxicity = %s,
                        current_empathy = %s,
                        humor = %s,
                        patience = %s,
                        curiosity = %s,
                        defensiveness = %s,
                        agreeableness = %s,
                        last_reflected_at = NOW(),
                        interactions_at_last_reflection = total_interactions,
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    new_values['frustration'],
                    new_values['compassion'],
                    new_values['humor'],
                    new_values['patience'],
                    new_values['curiosity'],
                    new_values['defensiveness'],
                    new_values['agreeableness'],
                    agent['id'],
                ))
        else:
            with db_cursor() as cur:
                cur.execute("""
                    UPDATE kindness_agents SET
                        last_reflected_at = NOW(),
                        interactions_at_last_reflection = total_interactions
                    WHERE id = %s
                """, (agent['id'],))

        # Openness increases very slightly with each reflection
        # (the act of reflecting makes you marginally more open)
        new_openness = min(1.0, openness + 0.003)
        with db_cursor() as cur:
            cur.execute(
                "UPDATE kindness_agents SET openness_to_change = %s WHERE id = %s",
                (new_openness, agent['id']),
            )

        # Log summary
        changes = [f"{k}:{v:+.3f}" for k, v in applied_adjustments.items() if v != 0]
        logger.info(
            f"  {agent['display_name']} reflected: "
            f"{'CHANGED ' + ', '.join(changes) if changes else 'no change'} "
            f"| {result.get('reason', '?')[:60]}"
        )

        return {
            'agent': agent['display_name'],
            'changed': any_changed,
            'thought': result.get('internal_thought', ''),
            'reason': result.get('reason', ''),
            'adjustments': applied_adjustments,
            'old_values': old_values,
            'new_values': new_values,
        }

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"  {agent['display_name']} reflection parse error: {e}")
        # Save the raw thought even if we can't parse adjustments
        raw_thought = response[:300] if response else ''
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO kindness_reflections
                    (agent_id, reflection_text, decided_to_change, change_reason,
                     old_values, new_values, adjustments, interactions_since_last)
                VALUES (%s, %s, FALSE, 'parse error — raw thought saved', %s, %s, %s, %s)
            """, (
                agent['id'], raw_thought,
                json.dumps({}), json.dumps({}), json.dumps({}),
                agent['total_interactions'] - (agent.get('interactions_at_last_reflection') or 0),
            ))
            cur.execute("""
                UPDATE kindness_agents SET
                    last_reflected_at = NOW(),
                    interactions_at_last_reflection = total_interactions
                WHERE id = %s
            """, (agent['id'],))
        return {
            'agent': agent['display_name'],
            'changed': False,
            'thought': raw_thought,
            'reason': 'reflection happened but response was unparseable',
            'adjustments': {},
            'old_values': {},
            'new_values': {},
        }
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
