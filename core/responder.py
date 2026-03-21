"""
Agent Responder — Decides if/how agents respond to existing threads.
This is what makes conversations real: agents reply to each other,
argue, de-escalate, and build bridges over time.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from core import db_ops
from core.evaluator import generate_comment, evaluate_comment
from core.simulator import calculate_dopamine, update_persona, DEFAULT_CONFIG
from utilities.llm_router import set_telemetry_context

logger = logging.getLogger(__name__)


def get_open_threads(limit=5):
    """Get threads that are still open for responses."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.keywords
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE t.is_complete = FALSE
              AND (t.expires_at IS NULL OR t.expires_at > NOW())
              AND (SELECT COUNT(*) FROM kindness_comments WHERE thread_id = t.id) < COALESCE(t.max_comments, 30)
            ORDER BY t.created_at DESC
            LIMIT %s
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]


def get_thread_comments(thread_db_id):
    """Get all comments in a thread with agent info."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT c.*, a.agent_id as agent_slug, a.display_name, a.llm_backend,
                   a.political_lean, a.current_toxicity, a.current_empathy,
                   a.trigger_topics, a.defensiveness, a.curiosity,
                   a.agreeableness, a.vote_willingness
            FROM kindness_comments c
            JOIN kindness_agents a ON c.agent_id = a.id
            WHERE c.thread_id = %s
            ORDER BY c.position
        """, (thread_db_id,))
        return [dict(row) for row in cur.fetchall()]


def get_agents_not_in_thread(thread_db_id, limit=10):
    """Get active agents who haven't participated in this thread yet.
    Prioritizes agents who haven't talked recently (rotation)."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT a.*, COALESCE(
                (SELECT MAX(c.created_at) FROM kindness_comments c WHERE c.agent_id = a.id),
                '2000-01-01'::timestamptz
            ) as last_spoke
            FROM kindness_agents a
            WHERE a.is_active = TRUE
              AND a.id NOT IN (
                  SELECT DISTINCT agent_id FROM kindness_comments WHERE thread_id = %s
              )
            ORDER BY last_spoke ASC, RANDOM()
            LIMIT %s
        """, (thread_db_id, limit))
        return [dict(row) for row in cur.fetchall()]


def should_respond(agent, comment, thread_context):
    """
    Decide if this agent should respond to this comment/thread.
    Uses personality dimensions to drive engagement decisions.
    Returns: (should_engage: bool, reason: str, target_comment: dict or None)
    """
    score = 0.0
    reason = []

    # Direct reply to my comment — almost always respond
    if comment and comment.get('replied_to_agent_id') == agent['id']:
        score += 0.7
        reason.append("replied to me")

    # Someone mentioned my comment or challenged my view
    if comment and _was_challenged(agent, comment, thread_context):
        score += agent.get('defensiveness', 5) * 0.06  # 0-0.6 boost
        reason.append("challenged")

    # Trigger topic match
    agent_triggers = agent.get('trigger_topics', [])
    if isinstance(agent_triggers, str):
        import json
        try:
            agent_triggers = json.loads(agent_triggers)
        except:
            agent_triggers = []

    thread_keywords = thread_context.get('keywords', [])
    if isinstance(thread_keywords, str):
        import json
        try:
            thread_keywords = json.loads(thread_keywords)
        except:
            thread_keywords = []

    if any(kw in thread_keywords for kw in agent_triggers):
        score += 0.4
        reason.append("trigger topic")

    # High empathy + conflict in thread → bridge-builder jumps in
    if _detect_conflict(thread_context) and agent.get('current_empathy', 5) > 7:
        score += 0.3
        reason.append("conflict mediator")

    # Curiosity-driven engagement
    if agent.get('curiosity', 5) > 7 and len(thread_context.get('comments', [])) < 10:
        score += 0.2
        reason.append("curious")

    # High toxicity + controversial topic → trolls jump in
    if agent.get('current_toxicity', 5) > 6 and thread_context.get('topic_type') == 'controversial':
        score += 0.3
        reason.append("troll attracted")

    # Controversial threads naturally draw more engagement
    if thread_context.get('topic_type') == 'controversial':
        score += 0.15
        reason.append("controversial")

    # Threads with existing comments are more interesting
    comment_count = len(thread_context.get('comments', []))
    if 3 <= comment_count <= 15:
        score += 0.1  # active thread, good to join

    # Agreeableness — agreeable agents join to support, disagreeable to challenge
    if agent.get('agreeableness', 5) > 7:
        score += 0.1
        reason.append("agreeable")

    # Lurker penalty (but floor at 0.3 so lurkers still occasionally engage)
    score *= max(0.3, agent.get('vote_willingness', 0.5))

    # Random factor
    score += random.uniform(-0.1, 0.1)

    should = score > 0.25  # Lower threshold — we want active conversations
    return should, ', '.join(reason) if reason else 'random', comment


def _was_challenged(agent, comment, thread_context):
    """Check if someone's comment challenges this agent's previous statements."""
    # Simple heuristic: if the comment has high toxicity and mentions disagreement
    if comment.get('toxicity_score', 0) and comment['toxicity_score'] >= 5:
        # Check if this agent previously commented in the thread
        for c in thread_context.get('comments', []):
            if c.get('agent_id') == agent['id']:
                return True
    return False


def _detect_conflict(thread_context):
    """Check if there's conflict in the thread worth mediating."""
    comments = thread_context.get('comments', [])
    if len(comments) < 2:
        return False
    # Conflict = recent comments have high toxicity
    recent = comments[-3:]
    avg_tox = sum(c.get('toxicity_score', 0) or 0 for c in recent) / max(len(recent), 1)
    return avg_tox > 5


def build_reply_context(thread_context, target_comment=None):
    """Build context for a reply — parent chain instead of flat last 5."""
    comments = thread_context.get('comments', [])

    if target_comment and target_comment.get('parent_comment_id'):
        # Walk the parent chain
        chain = []
        current_id = target_comment['id']
        comment_map = {c['id']: c for c in comments}

        while current_id and current_id in comment_map:
            chain.append(comment_map[current_id])
            current_id = comment_map[current_id].get('parent_comment_id')

        chain.reverse()
        return chain[-5:]  # Last 5 in the parent chain
    else:
        # Flat: return last 5 comments
        return comments[-5:]


def run_agent_responses(config=None):
    """
    Main response loop. Called by cron every 10 minutes.

    Staggered like real humans:
    - Random batch size per call (1-4 responses across 1-2 threads)
    - 20% chance of a quiet period (nobody responds)
    - Agents who haven't spoken recently get priority (rotation)
    - Agents whose backends are in backoff are silently skipped
    - Reactions happen ~50% of the time, from 2-3 random browsers
    """
    config = config or DEFAULT_CONFIG
    from utilities.usage_limiter import is_backend_in_backoff

    # Pick up to 3 open threads
    open_threads = get_open_threads(limit=5)
    if not open_threads:
        logger.info("No open threads for responses")
        return {'threads_checked': 0, 'responses': 0, 'reactions': 0}

    threads_to_check = random.sample(open_threads, min(random.randint(2, 3), len(open_threads)))

    # Higher batch cap — 3-6 responses per cycle
    max_responses_this_round = random.randint(3, 6)
    total_responses = 0

    for thread in threads_to_check:
        if total_responses >= max_responses_this_round:
            break

        comments = get_thread_comments(thread['id'])
        if not comments:
            continue

        thread_context = {
            'topic_type': thread.get('topic_type'),
            'keywords': thread.get('keywords', []),
            'comments': comments,
            'post_text': thread.get('post_text', ''),
        }

        # Candidates: prioritize agents who haven't spoken recently
        potential_responders = get_agents_not_in_thread(thread['id'], limit=8)

        # Also include existing participants (for reply-backs)
        existing_agent_ids = list(set(c['agent_id'] for c in comments))
        existing_agents = []
        if existing_agent_ids:
            from utilities.postgres_utils import db_cursor
            with db_cursor(dict_cursor=True) as cur:
                cur.execute(
                    "SELECT * FROM kindness_agents WHERE id = ANY(%s)",
                    (existing_agent_ids,)
                )
                existing_agents = [dict(row) for row in cur.fetchall()]

        # New agents first (rotation), then existing (reply-backs)
        all_candidates = potential_responders + existing_agents

        latest_comment = comments[-1] if comments else None

        for agent in all_candidates:
            if total_responses >= max_responses_this_round:
                break

            # Skip agents whose backend is in backoff — don't waste the call
            agent_backend = agent.get('llm_backend', 'groq')
            if is_backend_in_backoff(agent_backend):
                continue

            should, reason, target = should_respond(agent, latest_comment, thread_context)
            if not should:
                continue

            logger.info(f"  {agent['display_name']} responding to thread {thread['thread_id']} ({reason})")
            set_telemetry_context(agent_id=agent.get('agent_id'), thread_id=thread['thread_id'])

            reply_context = build_reply_context(thread_context, target)
            thread_history = [{'persona': c, 'comment': c.get('comment_text', ''), 'scores': {
                'kindness': c.get('kindness_score', 5),
                'toxicity': c.get('toxicity_score', 5),
                'empathy': c.get('empathy_score', 5),
                'bridge': c.get('bridge_score', 0),
            }} for c in reply_context]

            topic = {'post_text': thread['post_text'], 'topic_id': thread.get('topic_id', '?')}
            position = len(comments) + total_responses

            try:
                comment_text, actual_backend, gen_time_ms = generate_comment(
                    agent, topic, thread_history, position, config
                )

                scores, eval_time_ms = evaluate_comment(
                    comment_text, agent, thread_history, topic, config
                )

                dopamine, source, multiplier = calculate_dopamine(
                    scores, agent, position, thread_history, config
                )

                update_persona(agent, scores, dopamine)
                db_ops.update_agent_state(agent['id'], agent)

                parent_id = target['id'] if target and isinstance(target, dict) and 'id' in target else None
                replied_to = target.get('agent_id') if target and isinstance(target, dict) else None

                db_ops.save_comment(
                    thread['id'], agent['id'], position, comment_text, scores,
                    dopamine, source, multiplier, actual_backend,
                    gen_time_ms, eval_time_ms,
                )

                if parent_id or replied_to:
                    from utilities.postgres_utils import db_cursor as _dc
                    with _dc() as cur:
                        cur.execute("""
                            UPDATE kindness_comments
                            SET parent_comment_id = %s, replied_to_agent_id = %s
                            WHERE thread_id = %s AND position = %s
                        """, (parent_id, replied_to, thread['id'], position))

                total_responses += 1
                logger.info(f"    -> K:{scores['kindness']} T:{scores['toxicity']} +{dopamine}dp ({source})")

            except Exception as e:
                logger.error(f"    Response failed for {agent['display_name']}: {e}")

        # Check if thread should close
        total_comments = len(comments) + total_responses
        max_comments = thread.get('max_comments', 30)
        if total_comments >= max_comments:
            all_comments = get_thread_comments(thread['id'])
            avg_k = sum(c.get('kindness_score', 0) or 0 for c in all_comments) / max(len(all_comments), 1)
            avg_t = sum(c.get('toxicity_score', 0) or 0 for c in all_comments) / max(len(all_comments), 1)
            bridges = sum(1 for c in all_comments if (c.get('bridge_score') or 0) >= 7)
            db_ops.complete_thread(thread['id'], avg_k, avg_t, bridges)
            logger.info(f"  Thread {thread['thread_id']} completed at {total_comments} comments")

    # ── REACTION PHASE ──
    # 50% chance of reactions — lightweight browsing
    total_reactions = 0
    if random.random() < 0.5 and threads_to_check:
        total_reactions = react_to_comments(threads_to_check)

    return {
        'threads_checked': len(threads_to_check),
        'responses': total_responses,
        'reactions': total_reactions,
    }


def react_to_comments(threads):
    """A couple agents browse a thread and maybe react. Lightweight."""
    total = 0

    for thread in threads:
        comments = get_thread_comments(thread['id'])
        if len(comments) < 2:
            continue

        # 2-3 random agents glance at the thread (not 8)
        from utilities.postgres_utils import db_cursor as _dc
        with _dc(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM kindness_agents WHERE is_active = TRUE ORDER BY RANDOM() LIMIT %s",
                        (random.randint(2, 3),))
            browsers = [dict(row) for row in cur.fetchall()]

        for agent in browsers:
            # Lurkers react more than they comment — use vote_willingness * 2
            if random.random() > min(1.0, agent.get('vote_willingness', 0.5) * 2):
                continue

            # Pick a comment to react to (prefer kind comments)
            eligible = [c for c in comments if c['agent_id'] != agent['id']]
            if not eligible:
                continue

            # Weight toward higher-kindness comments (kind content gets more reactions)
            weights = [(c.get('kindness_score', 5) or 5) for c in eligible]
            chosen = random.choices(eligible, weights=weights, k=1)[0]

            # Pick reaction type based on personality
            if agent.get('current_empathy', 5) > 7:
                reaction = random.choice(['heart', 'heart', 'thumbsup'])
            elif agent.get('humor', 5) > 7:
                reaction = random.choice(['thumbsup', 'thumbsup', 'heart'])
            else:
                reaction = 'thumbsup'

            if db_ops.save_reaction(chosen['id'], agent['id'], reaction):
                total += 1
                # Tiered dopamine for the comment author based on how kind the comment was
                k = chosen.get('kindness_score', 0) or 0
                if k >= 6:
                    bonus = 5 if k <= 7 else (10 if k <= 9 else 15)
                    if reaction == 'heart':
                        bonus += 3
                    if (chosen.get('bridge_score', 0) or 0) >= 7:
                        bonus += 10
                    with _dc() as cur:
                        cur.execute(
                            "UPDATE kindness_agents SET total_dopamine = total_dopamine + %s WHERE id = %s",
                            (bonus, chosen['agent_id'])
                        )

    return total
