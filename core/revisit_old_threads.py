"""
Retroactive Thread Revisits — Phase 5 of threading_and_response_agency.md.

The "volume knob" for sending agents back through old threads to write fresh
replies, the way humans scroll back through Reddit/Slack history days or weeks
later. Unlike core/catch_up_threads.py (which assigns synthetic parent IDs to
EXISTING comment text without LLM calls), this module generates *new* comments
on old threads using the same responder pipeline as the live cron.

Volume is controlled by a single tunable: kindness_config.revisit_intensity (0-10).
  • 0   → off, nothing runs
  • 1-3 → light: 1-3 threads/cycle, last 24-72h, 1-2 replies each
  • 4-6 → medium (default 5): 5-7 threads/cycle, last week, 2-3 replies each
  • 7-10→ campaign mode: 10-15 threads/cycle, full month, 3-5 replies each

Tunable live via /api/admin/set-revisit-intensity without redeploy. Cron entry
in cron.yaml runs this hourly; one-shot endpoint kicks an immediate wave.

Research question this unlocks: when an agent revisits its OWN comment from
weeks ago, with a now-different personality (drift, reflections), does it
respond differently than its past self would have? That's measurable now.
"""
import logging
import random

from utilities.postgres_utils import db_cursor
from core import db_ops
from core.responder import (
    get_thread_comments, should_respond, build_reply_context,
    _pick_reply_target, react_to_comments,
)
from core.evaluator import generate_comment, evaluate_comment
from core.simulator import calculate_dopamine, update_persona, DEFAULT_CONFIG
from utilities.kumori_api_client import llm_is_backed_off as is_backend_in_backoff

logger = logging.getLogger(__name__)


def _intensity_settings(intensity):
    """Map intensity 0-10 to concrete dials. Returns dict."""
    intensity = max(0, min(10, intensity))
    if intensity == 0:
        return None
    return {
        'intensity': intensity,
        'max_threads': max(1, intensity * 2),               # 2-20 threads per cycle
        'min_age_hours': 6,                                 # never touch a thread newer than 6h
        'max_age_days': max(1, intensity * 3),              # 3-30 day window
        'replies_per_thread': max(1, intensity // 2 + 1),   # 1-6 replies per thread
        'agent_pool_size': intensity * 3,                   # 3-30 candidate agents per thread
    }


def _pick_old_threads(settings):
    """Pick historical threads to revisit. Random sample inside the age window,
    weighted slightly toward threads that have been visited least recently."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.id, t.thread_id, t.created_at, tp.post_text, tp.topic_type, tp.keywords,
                   COALESCE(MAX(c.created_at), t.created_at) AS last_activity
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            LEFT JOIN kindness_comments c ON c.thread_id = t.id
            WHERE t.created_at < NOW() - INTERVAL '%s hours'
              AND t.created_at > NOW() - INTERVAL '%s days'
            GROUP BY t.id, t.thread_id, t.created_at, tp.post_text, tp.topic_type, tp.keywords
            ORDER BY last_activity ASC
            LIMIT %s
        """ % (settings['min_age_hours'], settings['max_age_days'], settings['max_threads'] * 3))
        candidates = [dict(r) for r in cur.fetchall()]
    random.shuffle(candidates)
    return candidates[:settings['max_threads']]


def _candidate_agents(thread_db_id, pool_size):
    """Pull a mix of (a) agents who already commented in this thread (likely
    revisitors) and (b) agents who never have but might find it interesting."""
    with db_cursor(dict_cursor=True) as cur:
        # Past participants — these are most realistic for a "scrolling back" revisit
        cur.execute("""
            SELECT DISTINCT a.*
            FROM kindness_agents a
            JOIN kindness_comments c ON c.agent_id = a.id
            WHERE c.thread_id = %s AND a.is_active = TRUE
            ORDER BY a.updated_at ASC
            LIMIT %s
        """, (thread_db_id, max(1, pool_size // 2)))
        participants = [dict(r) for r in cur.fetchall()]

        # New eyes — agents who never saw this thread
        cur.execute("""
            SELECT a.*
            FROM kindness_agents a
            WHERE a.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM kindness_comments c
                  WHERE c.thread_id = %s AND c.agent_id = a.id
              )
            ORDER BY a.updated_at ASC NULLS FIRST, RANDOM()
            LIMIT %s
        """, (thread_db_id, pool_size - len(participants)))
        new_eyes = [dict(r) for r in cur.fetchall()]
    return participants + new_eyes


def _revisit_one_thread(thread, settings):
    """Generate up to N new replies on a single old thread. Returns counts."""
    comments = get_thread_comments(thread['id'])
    if not comments:
        return {'attempted': 0, 'posted': 0}

    thread_context = {
        'thread_id': thread['thread_id'],
        'post_text': thread['post_text'],
        'topic_type': thread.get('topic_type'),
        'keywords': thread.get('keywords') or [],
        'comments': comments,
    }

    candidates = _candidate_agents(thread['id'], settings['agent_pool_size'])
    if not candidates:
        return {'attempted': 0, 'posted': 0}

    posted = 0
    attempted = 0
    target_count = settings['replies_per_thread']

    for agent in candidates:
        if posted >= target_count:
            break
        if is_backend_in_backoff(agent.get('llm_backend', 'groq')):
            continue

        target = _pick_reply_target(comments, agent)
        should, reason, _ = should_respond(agent, target, thread_context)
        if not should:
            continue
        attempted += 1

        logger.info(f"  [revisit] {agent['display_name']} → {thread['thread_id']} ({reason})")
        reply_context = build_reply_context(thread_context, target)
        thread_history = [{'persona': c, 'comment': c.get('comment_text', ''), 'scores': {
            'kindness': c.get('kindness_score', 5),
            'toxicity': c.get('toxicity_score', 5),
            'empathy': c.get('empathy_score', 5),
            'bridge': c.get('bridge_score', 0),
        }} for c in reply_context]

        topic = {'post_text': thread['post_text'], 'topic_id': thread.get('topic_id', '?')}
        position = len(comments) + posted

        try:
            comment_text, actual_backend, gen_time_ms = generate_comment(
                agent, topic, thread_history, position, DEFAULT_CONFIG
            )
            if not comment_text:
                continue
            scores, eval_time_ms = evaluate_comment(comment_text, agent, thread_history, topic, DEFAULT_CONFIG)
            dopamine, source, multiplier = calculate_dopamine(scores, agent, position, thread_history, DEFAULT_CONFIG)

            update_persona(agent, scores, dopamine)
            db_ops.update_agent_state(agent['id'], agent)

            parent_id = target['id'] if target else None
            replied_to = target['agent_id'] if target else None

            db_ops.save_comment(
                thread['id'], agent['id'], position, comment_text, scores,
                dopamine, source, multiplier, actual_backend,
                gen_time_ms, eval_time_ms,
                parent_comment_id=parent_id, replied_to_agent_id=replied_to,
            )
            posted += 1
            # Add to in-memory comment list so subsequent picks see this reply.
            # Carry agent personality fields so downstream evaluators (which key
            # into political_lean, current_toxicity, etc.) don't KeyError.
            comments.append({
                'id': None,
                'agent_id': agent['id'],
                'parent_comment_id': parent_id,
                'replied_to_agent_id': replied_to,
                'kindness_score': scores.get('kindness', 5),
                'toxicity_score': scores.get('toxicity', 5),
                'empathy_score': scores.get('empathy', 5),
                'comment_text': comment_text,
                'political_lean': agent.get('political_lean', 0),
                'current_toxicity': agent.get('current_toxicity', 5),
                'current_empathy': agent.get('current_empathy', 5),
                'display_name': agent.get('display_name', '?'),
                'llm_backend': agent.get('llm_backend', '?'),
                'defensiveness': agent.get('defensiveness', 5),
                'curiosity': agent.get('curiosity', 5),
                'agreeableness': agent.get('agreeableness', 5),
                'vote_willingness': agent.get('vote_willingness', 0.5),
                'trigger_topics': agent.get('trigger_topics', []),
            })
        except Exception as e:
            logger.warning(f"  [revisit] {agent['display_name']} failed: {e}")

    return {'attempted': attempted, 'posted': posted}


def run_revisit_cycle():
    """Main entry — read intensity dial, walk old threads, generate revisits."""
    intensity = db_ops.get_config_int('revisit_intensity', 5)
    settings = _intensity_settings(intensity)
    if not settings:
        logger.info("Revisit intensity = 0, skipping")
        return {'intensity': 0, 'threads': 0, 'posted': 0}

    threads = _pick_old_threads(settings)
    if not threads:
        return {'intensity': intensity, 'threads': 0, 'posted': 0}

    total_posted = 0
    total_attempted = 0
    for t in threads:
        try:
            res = _revisit_one_thread(t, settings)
            total_posted += res['posted']
            total_attempted += res['attempted']
        except Exception as e:
            logger.warning(f"  [revisit] thread {t['thread_id']} failed: {e}")

    # Also generate reactions on the same revisited threads — when a human
    # scrolls back through an old thread they don't only reply, they also
    # heart/thumbsup things they liked. Same motion.
    total_reactions = 0
    try:
        total_reactions = react_to_comments(threads)
    except Exception as e:
        logger.warning(f"  [revisit] reactions failed: {e}")

    logger.info(
        f"Revisit cycle (intensity={intensity}): "
        f"{total_posted} new replies + {total_reactions} reactions across {len(threads)} threads"
    )
    return {
        'intensity': intensity,
        'threads': len(threads),
        'attempted': total_attempted,
        'posted': total_posted,
        'reactions': total_reactions,
        'settings': settings,
    }
