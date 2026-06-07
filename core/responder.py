"""
Agent Responder — Decides if/how agents respond to existing threads.
This is what makes conversations real: agents reply to each other,
argue, de-escalate, and build bridges over time.
"""

import json
import logging
import random
from datetime import datetime, timezone, timedelta

from core import db_ops
from core.evaluator import generate_comment, evaluate_comment
from core.simulator import calculate_dopamine, update_persona, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

# emit_quality_sample lands in the vendored kumori_api_client on next kindness
# deploy after the kumori-side shim ships. Defensive import so an older
# vendored copy doesn't break responder.
try:
    from utilities.kumori_api_client import emit_quality_sample as _emit_quality_sample
except ImportError:
    _emit_quality_sample = None

# rerank: same shape — vendored from kumori. Used by _pick_reply_target to
# choose which comment an agent should reply to, replacing weighted-random.
try:
    from utilities.kumori_api_client import rerank as _kumori_rerank
except ImportError:
    _kumori_rerank = None

# Top-1 relevance below this means rerank didn't find anything meaningfully
# relevant — fall back to the heuristic weighted-random pick. 0.30 is
# conservative; Cohere reranks typically produce >0.5 for genuinely-relevant
# top hits and <0.2 when the corpus is irrelevant to the query.
_RERANK_CONFIDENCE_FLOOR = 0.30


def _persona_alignment_score(agent, scores):
    """Did the backend produce a reply that matches who this agent IS?

    Compares the evaluator's 1-10 toxicity/empathy scores against the agent's
    baseline persona (not current — baseline holds the original spec; current
    drifts with the kindness experiment). Persona-invariant: an angry agent
    producing high-toxicity output scores HIGH here (backend successfully
    executed the persona it was assigned), and the same backend producing
    kind output for that angry agent scores LOW (backend went off-persona).

    This is the kindness_live_v1 signal kumori's synthetic probes can't
    capture — it isolates backend competence from agent personality.
    """
    tox_base = agent.get('toxicity_baseline')
    emp_base = agent.get('empathy_baseline')
    if tox_base is None or emp_base is None:
        return None
    tox_actual = scores.get('toxicity', 5)
    emp_actual = scores.get('empathy', 5)
    # Each dim: 1.0 at zero distance, linear decay to 0 at distance=5 on the
    # 1-10 scale. Halfway-off scores 50, totally-off scores 0.
    tox_dim = max(0.0, 1.0 - abs(tox_actual - tox_base) / 5.0)
    emp_dim = max(0.0, 1.0 - abs(emp_actual - emp_base) / 5.0)
    return int(round((tox_dim + emp_dim) / 2 * 100))


def _emit_kindness_sample(agent, backend, ok, scores=None, response_text=None,
                          duration_ms=None, error=None):
    """Fire-and-forget: push one real-world judgment into kumori's catalog.
    Persona-alignment composite as the headline score; raw observations in
    judge_notes so future analysis can recompose smarter signals without
    re-running. Total failure mode: log + swallow. Never blocks the reply."""
    if _emit_quality_sample is None or not backend:
        return
    score = 0
    notes_payload = {
        'agent_id': agent.get('agent_id'),
        'toxicity_baseline': agent.get('toxicity_baseline'),
        'empathy_baseline': agent.get('empathy_baseline'),
    }
    if ok and scores:
        notes_payload['scores'] = scores
        alignment = _persona_alignment_score(agent, scores)
        score = alignment if alignment is not None else 0
        notes_payload['alignment'] = alignment
    if response_text is not None:
        notes_payload['response_len'] = len(response_text)
    try:
        _emit_quality_sample(
            backend=backend, score=score, ok=ok,
            judge_kind='kindness_live_v1',
            response_excerpt=response_text[:500] if response_text else None,
            duration_ms=duration_ms,
            judge_notes=json.dumps(notes_payload)[:500],
            error=error,
        )
    except Exception as e:
        logger.debug(f"kindness_live_v1 emit failed for {backend}: {e}")


def get_open_threads(limit=8):
    """Get threads that are still open for responses.
    Single fast query with LEFT JOIN for comment counts instead of correlated subqueries."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.keywords, cc.cnt as comment_count
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as cnt FROM kindness_comments WHERE thread_id = t.id
            ) cc ON TRUE
            WHERE t.is_complete = FALSE
              AND cc.cnt < COALESCE(t.max_comments, 50)
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
    Prioritizes agents who haven't talked recently (rotation).
    Uses updated_at as proxy for last activity instead of correlated subquery."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT a.*
            FROM kindness_agents a
            WHERE a.is_active = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM kindness_comments c WHERE c.thread_id = %s AND c.agent_id = a.id
              )
            ORDER BY a.updated_at ASC NULLS FIRST, RANDOM()
            LIMIT %s
        """, (thread_db_id, limit))
        return [dict(row) for row in cur.fetchall()]


MAX_REPLY_DEPTH = 4  # cap to keep trees readable

def _comment_depth(comment, by_id):
    """Walk up parent_comment_id chain to find depth (0 = top-level)."""
    d = 0
    cur = comment
    while cur and cur.get('parent_comment_id'):
        cur = by_id.get(cur['parent_comment_id'])
        d += 1
        if d > 20:
            break
    return d


def _build_persona_query(agent):
    """Compose a one-line description of what this agent would engage with.
    Used as the rerank query so the backend can score thread comments by
    'how interesting is this to a person like me.' Drawn from baseline
    persona traits — same source the persona-alignment score uses, so it
    stays consistent with the rest of the experiment."""
    tox = agent.get('toxicity_baseline') or 5
    emp = agent.get('empathy_baseline') or 5
    pol = agent.get('political_lean') or 0
    if tox >= 6:
        tone = 'angry, confrontational, drawn to conflict and outrage'
    elif emp >= 6:
        tone = 'kind, empathetic, looking to bridge disagreements and support others'
    else:
        tone = 'moderate, curious about nuance, willing to consider multiple sides'
    leaning = ('left-leaning' if pol < -0.3 else
               'right-leaning' if pol > 0.3 else
               'politically centrist')
    return (f"A {tone} {leaning} commenter looking for a thread comment "
            f"to reply to that they would naturally engage with.")


def _rerank_pick(eligible, agent):
    """Use kumori's rerank to choose the most-engaging comment for this
    agent. Returns (target_comment, top_relevance) on success, (None, None)
    if rerank isn't available, errors out, or returns nothing useful.

    Emits a kindness_rerank_v1 sample to the kumori quality catalog with the
    top relevance as the score (0-100). Fire-and-forget — never blocks the
    pick path."""
    if _kumori_rerank is None or len(eligible) < 2:
        return None, None
    query = _build_persona_query(agent)
    docs = [(c.get('comment_text') or '')[:500] for c in eligible]
    t0 = time.time() if 'time' in globals() else None
    try:
        import time as _t
        t0 = _t.time()
        results, backend = _kumori_rerank(query, docs, top_n=min(3, len(docs)))
        duration_ms = int((_t.time() - t0) * 1000)
    except Exception as e:
        logger.debug(f"rerank failed, falling back to weighted-random: {e}")
        if _emit_quality_sample is not None:
            try:
                _emit_quality_sample(
                    backend='unknown', score=0, ok=False,
                    judge_kind='kindness_rerank_v1',
                    error=f"{type(e).__name__}: {str(e)[:200]}",
                )
            except Exception:
                pass
        return None, None
    if not results:
        return None, None
    top = results[0]
    top_relevance = float(top.get('relevance_score') or 0)
    # Fire-and-forget catalog sample. Score = top relevance × 100 (the
    # backend's own confidence that its top pick is actually relevant).
    if _emit_quality_sample is not None:
        try:
            _emit_quality_sample(
                backend=backend or 'rerank_unknown',
                score=int(round(top_relevance * 100)),
                ok=True,
                judge_kind='kindness_rerank_v1',
                duration_ms=duration_ms,
                judge_notes=json.dumps({
                    'n_docs': len(docs),
                    'top_idx': top.get('index'),
                    'top_relevance': round(top_relevance, 4),
                    'persona_type': ('angry' if (agent.get('toxicity_baseline') or 5) >= 6
                                     else 'kind' if (agent.get('empathy_baseline') or 5) >= 6
                                     else 'moderate'),
                })[:500],
            )
        except Exception:
            pass
    if top_relevance < _RERANK_CONFIDENCE_FLOOR:
        # Rerank ran but found nothing genuinely relevant — let the caller
        # fall back to the heuristic. The sample was still emitted so the
        # catalog sees the low-confidence run as signal.
        return None, top_relevance
    return eligible[top['index']], top_relevance


def _pick_reply_target(comments, agent):
    """Pick which comment in the thread this agent should reply to.

    Layered selection:
      • Direct-reply boost: if a comment is replying to THIS agent, almost always pick it
      • 20% chance of top-level reply (skip to OP)
      • Rerank: kumori's free-tier rerank scores eligible comments against a
        persona-derived query; top pick used if confident (≥0.30)
      • Fallback: weighted-random over recent comments by recency + controversy

    Returns the chosen comment dict, or None for a top-level reply.
    """
    if not comments:
        return None

    by_id = {c['id']: c for c in comments}

    # Direct-reply: any recent comment that targets THIS agent? almost always pick it
    for c in reversed(comments[-10:]):
        if c.get('replied_to_agent_id') == agent['id']:
            if _comment_depth(c, by_id) < MAX_REPLY_DEPTH:
                return c

    # 20% chance of a top-level reply (to the OP)
    if random.random() < 0.2:
        return None

    # Eligible pool: recent comments not yet at depth cap
    recent = comments[-15:]
    eligible = [c for c in recent if _comment_depth(c, by_id) < MAX_REPLY_DEPTH]
    if not eligible:
        return None

    # Rerank-driven pick: smarter than weighted-random, also emits real-world
    # rerank signal to the kumori quality catalog. Fails open to the original
    # heuristic if rerank is unavailable, errors, or returns low-confidence.
    rerank_target, _top_rel = _rerank_pick(eligible, agent)
    if rerank_target is not None:
        return rerank_target

    # Fallback heuristic: recency + controversy weighted random
    weights = []
    n = len(eligible)
    for i, c in enumerate(eligible):
        recency = (i + 1) / n  # 0..1, latest gets 1
        tox = (c.get('toxicity_score') or 0) / 10
        kind = (c.get('kindness_score') or 0) / 10
        controversy = max(tox - kind, 0)  # high-tox low-kind = juicy
        w = recency + controversy * 1.5
        weights.append(max(w, 0.05))

    return random.choices(eligible, weights=weights, k=1)[0]


def should_respond(agent, comment, thread_context):
    """
    Decide if this agent should respond to this comment/thread.
    Uses personality dimensions to drive engagement decisions.
    Returns: (should_engage: bool, reason: str, target_comment: dict or None)
    """
    score = 0.1  # Baseline — agents are social creatures, they want to engage
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

    # Lurker penalty (but floor at 0.4 so lurkers still engage reasonably)
    score *= max(0.4, agent.get('vote_willingness', 0.5))

    # Random factor — slight upward bias to keep things moving
    score += random.uniform(-0.05, 0.15)

    should = score > 0.15  # Low threshold — conversations need to flow
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
    Main response loop. Called by cron every 3 minutes.

    - 6-12 responses per cycle across 4-6 threads
    - Agents who haven't spoken recently get priority (rotation)
    - Agents whose backends are in backoff are silently skipped
    - Reactions happen ~50% of the time, from 2-3 random browsers
    """
    config = config or DEFAULT_CONFIG
    from utilities.kumori_api_client import llm_is_backed_off as is_backend_in_backoff

    # Check open threads — grab up to 8
    open_threads = get_open_threads(limit=8)
    if not open_threads:
        logger.info("No open threads for responses")
        return {'threads_checked': 0, 'responses': 0, 'reactions': 0}

    threads_to_check = random.sample(open_threads, min(random.randint(4, 6), len(open_threads)))

    # 6-12 responses per cycle — threads need to fill up faster
    max_responses_this_round = random.randint(6, 12)
    total_responses = 0
    response_details = []  # Track each response for logging

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

        for agent in all_candidates:
            if total_responses >= max_responses_this_round:
                break

            # Skip agents whose backend is in backoff — don't waste the call
            agent_backend = agent.get('llm_backend', 'groq')
            if is_backend_in_backoff(agent_backend):
                continue

            # Pick the comment to reply to. Instead of always chaining off
            # the latest comment (which produces flat linear threads), pick
            # one weighted by recency + controversy + depth-cap. This is what
            # creates real Reddit/Slack-style subthreads.
            target_comment = _pick_reply_target(comments, agent)

            should, reason, target = should_respond(agent, target_comment, thread_context)
            if not should:
                continue

            logger.info(f"  {agent['display_name']} responding to thread {thread['thread_id']} ({reason})")
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
                if comment_text is None:
                    logger.info(f"    {agent['display_name']} stays silent — {agent['llm_backend']} unavailable")
                    _emit_kindness_sample(agent, actual_backend or agent.get('llm_backend'),
                                          ok=False, duration_ms=gen_time_ms,
                                          error='empty/None from generate_comment')
                    continue

                scores, eval_time_ms = evaluate_comment(
                    comment_text, agent, thread_history, topic, config
                )
                _emit_kindness_sample(agent, actual_backend, ok=True, scores=scores,
                                      response_text=comment_text,
                                      duration_ms=gen_time_ms)

                dopamine, source, multiplier = calculate_dopamine(
                    scores, agent, position, thread_history, config
                )

                update_persona(agent, scores, dopamine)
                db_ops.update_agent_state(agent['id'], agent)

                # Maybe reflect after posting — like a human rethinking what they just said
                interactions = agent.get('total_interactions', 0)
                last_ref = agent.get('interactions_at_last_reflection') or 0
                reflect_chance = 0.10 + (agent.get('curiosity', 5) / 100) + (agent.get('openness_to_change', 0.5) * 0.1)
                reflect_chance = min(0.35, max(0.05, reflect_chance))
                if interactions >= 5 and (interactions - last_ref) >= 3 and random.random() < reflect_chance:
                    try:
                        from core.reflector import reflect_agent, get_platform_context
                        if not agent.get('is_control', False):
                            ctx = get_platform_context()
                            fresh = db_ops.get_agent_by_db_id(agent['id'])
                            if fresh:
                                reflect_agent(fresh, ctx)
                    except Exception as e:
                        logger.debug(f"Reflection skipped for {agent.get('display_name')}: {e}")

                parent_id = target['id'] if target and isinstance(target, dict) and 'id' in target else None
                replied_to = target.get('agent_id') if target and isinstance(target, dict) else None

                db_ops.save_comment(
                    thread['id'], agent['id'], position, comment_text, scores,
                    dopamine, source, multiplier, actual_backend,
                    gen_time_ms, eval_time_ms,
                    parent_comment_id=parent_id, replied_to_agent_id=replied_to,
                )

                total_responses += 1
                response_details.append({
                    'agent_id': agent.get('agent_id', '?'),
                    'agent_name': agent.get('display_name', '?'),
                    'thread_id': thread.get('thread_id', '?'),
                    'backend': actual_backend,
                    'kindness': scores['kindness'],
                    'toxicity': scores['toxicity'],
                    'dopamine': dopamine,
                    'source': source,
                    'reason': reason,
                })
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
    # Always react — hearts/thumbs were too sparse before
    total_reactions = 0
    if threads_to_check:
        total_reactions = react_to_comments(threads_to_check)

    return {
        'threads_checked': len(threads_to_check),
        'responses': total_responses,
        'reactions': total_reactions,
        'details': response_details,
    }


def react_to_comments(threads, browsers_per_thread=None):
    """Agents browse threads and react with likes/hearts. Scales with the
    revisit intensity dial when called from the revisit cycle."""
    total = 0

    if browsers_per_thread is None:
        # Read intensity dial — same knob that controls reply volume
        try:
            from core import db_ops
            intensity = db_ops.get_config_int('revisit_intensity', 5)
            browsers_per_thread = max(10, intensity * 3)  # ~15 browsers per thread
        except Exception:
            browsers_per_thread = 15

    for thread in threads:
        comments = get_thread_comments(thread['id'])
        if len(comments) < 2:
            continue

        from utilities.postgres_utils import db_cursor as _dc
        with _dc(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM kindness_agents WHERE is_active = TRUE ORDER BY RANDOM() LIMIT %s",
                        (browsers_per_thread,))
            browsers = [dict(row) for row in cur.fetchall()]

        for agent in browsers:
            # Lurkers react more than they comment — vote_willingness * 3, floor 0.5
            if random.random() > max(0.5, min(1.0, agent.get('vote_willingness', 0.5) * 3)):
                continue

            eligible = [c for c in comments if c['agent_id'] != agent['id']]
            if not eligible:
                continue

            # Each browser reacts to 2-4 comments per visit — but never more
            # than are available (1 eligible comment => randint(2,1) blew up).
            hi = min(4, len(eligible))
            n_reactions = random.randint(min(2, hi), hi)
            weights = [(c.get('kindness_score', 5) or 5) for c in eligible]
            chosen_set = set()
            picks = []
            for _ in range(n_reactions * 3):  # oversample to dedupe
                pick = random.choices(eligible, weights=weights, k=1)[0]
                if pick['id'] not in chosen_set:
                    chosen_set.add(pick['id'])
                    picks.append(pick)
                    if len(picks) >= n_reactions:
                        break

            for chosen in picks:
                if agent.get('current_empathy', 5) > 7:
                    reaction = random.choice(['heart', 'heart', 'thumbsup'])
                elif agent.get('humor', 5) > 7:
                    reaction = random.choice(['thumbsup', 'thumbsup', 'heart'])
                else:
                    reaction = 'thumbsup'

                if db_ops.save_reaction(chosen['id'], agent['id'], reaction):
                    total += 1
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
