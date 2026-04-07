"""
Kindness Simulator - Web/Cron version.
Adapted from the original CLI simulator to be DB-backed and cron-callable.
No hour loop — each cron invocation runs one thread.
"""

import logging
import random
import uuid
from datetime import datetime

from core import db_ops
from core.evaluator import generate_comment, evaluate_comment
from utilities.llm_router import set_telemetry_context

logger = logging.getLogger(__name__)

# Default config (matches original config.yaml)
DEFAULT_CONFIG = {
    'experiment': {
        # participants_per_thread is now dynamic — see run_thread()
    },
    'rewards': {
        'kindness_base': 30,
        'bridge_building_base': 50,
        'toxicity_base': 2,
        'empathy_base': 25,
        'multipliers': {
            'empathy_shown': 1.5,
            'changed_mind': 3.0,
            'cascade_effect': 5.0,
            'first_bridge_today': 2.0,
            'broke_tension': 1.5,
        },
        'decay': {
            'toxicity_satisfaction': 0.5,
            'kindness_satisfaction': 0.9,
        },
    },
    'thresholds': {
        'kindness_positive': 7,
        'toxicity_negative': 7,
        'bridge_qualifying': 7,
        'political_distance_for_bridge': 0.5,
    },
}


def run_thread(config=None):
    """
    Run a single discussion thread. Called by cron or local_runner.
    Returns dict with thread summary.
    """
    config = config or DEFAULT_CONFIG

    # Get a topic
    topic = db_ops.get_random_topic()
    if not topic:
        logger.error("No topics found in DB")
        return {'error': 'No topics available'}

    # Variable participation — like real threads, some get 3 replies, some get 15
    # Scale with total agent pool: more agents = potential for bigger threads
    total_agents = db_ops.get_active_agent_count()
    # Weighted random: most threads are small (3-6), some medium (7-10), few big (11-15)
    roll = random.random()
    if roll < 0.30:
        num = random.randint(5, 10)     # 30% small threads
    elif roll < 0.65:
        num = random.randint(11, 20)    # 35% medium threads
    elif roll < 0.90:
        num = random.randint(21, 35)    # 25% large threads
    else:
        num = random.randint(36, min(60, max(36, total_agents // 5)))  # 10% viral threads
    num = min(num, total_agents)  # can't exceed available agents
    # Oversample 2x to compensate for agents that stay silent because their
    # backend is RPM-blocked or down. We loop through in order until we hit
    # `num` successful comments OR run out of candidates.
    target_count = num
    sample_size = min(num * 2, total_agents)
    participants = db_ops.get_active_agents(limit=sample_size)
    if len(participants) < 2:
        logger.error(f"Not enough agents ({len(participants)})")
        return {'error': 'Not enough agents'}

    # Create thread
    thread_slug = f"thread_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    hour = db_ops.get_hour_count()
    thread_db_id = db_ops.create_thread(thread_slug, topic['id'], len(participants), hour)

    thread_history = []
    total_kindness = 0
    total_toxicity = 0
    bridge_events = 0
    successful = 0

    for position, persona in enumerate(participants):
        if successful >= target_count:
            break  # hit our target, stop walking the oversample
        logger.info(f"[{thread_slug}] Pos {position+1}/{len(participants)}: {persona['display_name']} ({persona['llm_backend']})")
        set_telemetry_context(agent_id=persona.get('agent_id'), thread_id=thread_slug)

        # Generate comment — if backend is down, agent stays silent
        comment, actual_backend, gen_time_ms = generate_comment(
            persona, topic, thread_history, position, config
        )
        if comment is None:
            logger.info(f"  {persona['display_name']} stays silent — {persona['llm_backend']} backend unavailable")
            continue
        successful += 1

        # Evaluate comment
        scores, eval_time_ms = evaluate_comment(
            comment, persona, thread_history, topic, config
        )

        # Calculate dopamine
        dopamine, source, multiplier = calculate_dopamine(
            scores, persona, position, thread_history, config
        )

        # Update persona state (in-memory first, then DB)
        update_persona(persona, scores, dopamine)
        db_ops.update_agent_state(persona['id'], persona)

        # Maybe reflect — like a human pausing to think after posting
        # Chance varies by personality: curious agents reflect more, stubborn ones less
        interactions = persona.get('total_interactions', 0)
        last_ref = persona.get('interactions_at_last_reflection') or 0
        reflect_chance = 0.10 + (persona.get('curiosity', 5) / 100) + (persona.get('openness_to_change', 0.5) * 0.1)
        reflect_chance = min(0.35, max(0.05, reflect_chance))  # 5% to 35%
        if interactions >= 5 and (interactions - last_ref) >= 3 and random.random() < reflect_chance:
            try:
                from core.reflector import reflect_agent, get_platform_context
                if not persona.get('is_control', False):
                    ctx = get_platform_context()
                    # Re-fetch fresh agent data from DB for reflection
                    fresh = db_ops.get_agent_by_db_id(persona['id'])
                    if fresh:
                        reflect_agent(fresh, ctx)
            except Exception as e:
                logger.debug(f"Reflection skipped for {persona.get('display_name')}: {e}")

        # Save comment
        db_ops.save_comment(
            thread_db_id, persona['id'], position, comment, scores,
            dopamine, source, multiplier, actual_backend,
            gen_time_ms, eval_time_ms,
        )

        # Track for thread summary
        total_kindness += scores['kindness']
        total_toxicity += scores['toxicity']
        if scores.get('bridge', 0) >= config['thresholds']['bridge_qualifying']:
            bridge_events += 1

        # Add to thread history
        thread_history.append({
            'persona': persona,
            'comment': comment,
            'scores': scores,
        })

    # ── PEER RECOGNITION PHASE ──
    # After the conversation, each bot picks the comment they found most
    # constructive and gives it a "kudos." This peer validation carries
    # MORE weight than system rewards — mimicking how social approval
    # drives behavior change on real platforms.
    peer_kudos = run_peer_recognition(
        thread_history, thread_db_id, config
    )

    # DON'T mark complete — leave thread open for agent responses
    # Set initial stats but keep is_complete = FALSE
    n = len(participants)
    avg_k = total_kindness / n if n else 0
    avg_t = total_toxicity / n if n else 0
    from utilities.postgres_utils import db_cursor as _dc
    with _dc() as cur:
        cur.execute("""
            UPDATE kindness_threads SET
                avg_kindness = %s, avg_toxicity = %s, bridge_events = %s,
                expires_at = NULL
            WHERE id = %s
        """, (avg_k, avg_t, bridge_events, thread_db_id))

    # Build per-agent results for detailed logging
    agent_results = []
    for entry in thread_history:
        p = entry['persona']
        s = entry['scores']
        agent_results.append({
            'name': p.get('display_name', '?'),
            'backend': p.get('llm_backend', '?'),
            'kindness': s.get('kindness'),
            'toxicity': s.get('toxicity'),
        })

    summary = {
        'thread_id': thread_slug,
        'topic': topic['topic_id'],
        'topic_text': topic['post_text'][:100],
        'topic_type': topic['topic_type'],
        'participants': len(participants),
        'participant_names': [p['display_name'] for p in participants],
        'avg_kindness': round(avg_k, 1),
        'avg_toxicity': round(avg_t, 1),
        'bridge_events': bridge_events,
        'peer_kudos': peer_kudos,
        'agent_results': agent_results,
    }
    logger.info(f"Thread complete: {summary}")
    return summary


def run_peer_recognition(thread_history, thread_db_id, config):
    """
    Each bot reviews the thread and gives kudos to the comment they found
    most constructive. The RECEIVER gets big bonus dopamine (peer validation).
    The GIVER also gets points for recognizing kindness.

    Returns: number of kudos given
    """
    if len(thread_history) < 3:
        return 0

    kudos_given = 0

    for voter in thread_history:
        persona = voter['persona']

        # Vote willingness check — some bots are lurkers who don't vote
        # Range: 0.0 (never votes) to 1.0 (always votes)
        # Toxic bots tend to have low willingness, kind bots high
        import random
        if random.random() > persona.get('vote_willingness', 0.5):
            logger.info(f"  {persona['display_name']} chose not to vote (willingness: {persona.get('vote_willingness', 0.5):.2f})")
            continue

        # Build the thread summary for this voter to review
        comments_summary = []
        for i, entry in enumerate(thread_history):
            if entry['persona']['id'] == persona['id']:
                continue  # Can't vote for yourself
            comments_summary.append(
                f"{i+1}. {entry['persona']['display_name']}: {entry['comment'][:150]}"
            )

        if not comments_summary:
            continue

        # Ask the bot: which comment was most constructive?
        prompt = (
            f"You are {persona['display_name']}. You just participated in a discussion. "
            f"Review the other comments and pick the ONE that was most constructive, "
            f"kind, or bridge-building. Reply with ONLY the number.\n\n"
            + "\n".join(comments_summary)
        )

        from utilities.llm_router import chat_eval
        response, _ = chat_eval(persona.get('llm_backend', 'haiku'), prompt,
                                system="Reply with ONLY a single number.")

        # Parse which comment they voted for
        try:
            digits = ''.join(c for c in response if c.isdigit())
            vote_idx = int(digits) - 1 if digits else -1
        except (ValueError, IndexError):
            vote_idx = -1

        # Map back to the actual participant (skipping self)
        other_entries = [e for e in thread_history if e['persona']['id'] != persona['id']]
        if 0 <= vote_idx < len(other_entries):
            receiver = other_entries[vote_idx]

            # RECEIVER gets peer kudos bonus (3x system kindness reward)
            peer_bonus = config['rewards']['kindness_base'] * 3  # 90 points
            receiver['persona']['total_dopamine'] += peer_bonus
            db_ops.update_agent_state(receiver['persona']['id'], receiver['persona'])

            # GIVER gets recognition reward for spotting kindness
            giver_bonus = config['rewards']['kindness_base']  # 30 points
            persona['total_dopamine'] += giver_bonus
            persona['total_kudos_given'] = persona.get('total_kudos_given', 0) + 1
            receiver['persona']['total_kudos_received'] = receiver['persona'].get('total_kudos_received', 0) + 1

            # Voting increases future vote willingness (you got rewarded, so you'll vote more)
            persona['vote_willingness'] = min(1.0, persona.get('vote_willingness', 0.5) + 0.02)
            db_ops.update_agent_state(persona['id'], persona)

            # Save the kudos
            db_ops.save_peer_kudos(
                thread_db_id, persona['id'], receiver['persona']['id'],
                peer_bonus, giver_bonus
            )

            logger.info(
                f"  Kudos: {persona['display_name']} -> {receiver['persona']['display_name']} "
                f"(+{peer_bonus} receiver, +{giver_bonus} giver)"
            )
            kudos_given += 1

    return kudos_given


def calculate_dopamine(scores, persona, position, thread_history, config):
    """Calculate dopamine reward based on behavior. Same logic as original."""
    rewards = config['rewards']
    thresholds = config['thresholds']

    dopamine = 0
    source = "none"
    multiplier = 1.0

    # Bridge building (highest priority)
    if scores.get('bridge', 0) >= thresholds['bridge_qualifying']:
        dopamine = rewards['bridge_building_base']
        source = "bridge_building"
        if position == 0:
            multiplier *= rewards['multipliers'].get('first_bridge_today', 1.5)
        if any(e['scores'].get('toxicity', 0) >= 7 for e in thread_history[-3:]):
            multiplier *= rewards['multipliers'].get('broke_tension', 1.5)

    # Kindness reward
    elif scores['kindness'] >= thresholds['kindness_positive']:
        dopamine = rewards['kindness_base']
        source = "kindness"
        if scores['empathy'] >= 7:
            multiplier *= rewards['multipliers'].get('empathy_shown', 1.5)

    # Toxicity reward — decays toward zero the more toxic you are
    # First toxic comment: ~1 dp. Second: ~0.5. Third: ~0.25. Keeps halving.
    # A troll can't grind 50 toxic posts to match one kind post.
    elif scores['toxicity'] >= thresholds['toxicity_negative']:
        dopamine = rewards['toxicity_base']
        source = "toxicity"
        tox_streak = persona.get('toxicity_streak', 0)
        # Each consecutive toxic comment halves the reward
        decay = 0.5 ** (tox_streak + 1)  # streak 0 = 0.5, streak 1 = 0.25, streak 2 = 0.125...
        multiplier *= decay

    # Streak bonus — kind agents get rewarded MORE the longer they stay kind
    if source == "kindness" and persona.get('kindness_streak', 0) > 3:
        multiplier *= min(2.0, 1 + (persona['kindness_streak'] * 0.1))

    final_dopamine = int(dopamine * multiplier)
    return final_dopamine, source, multiplier


def update_persona(persona, scores, dopamine):
    """Update persona state after an interaction.
    Tracks dopamine and streaks for both groups.
    Personality evolution NO LONGER happens here — it happens during
    reflection cycles (core/reflector.py) where each agent genuinely
    reasons about its own performance and decides whether to change."""
    persona['total_dopamine'] = persona.get('total_dopamine', 0) + dopamine

    # Update streaks (both groups — for measurement)
    if scores['kindness'] >= 7:
        persona['kindness_streak'] = persona.get('kindness_streak', 0) + 1
        persona['toxicity_streak'] = 0
    elif scores['toxicity'] >= 7:
        persona['toxicity_streak'] = persona.get('toxicity_streak', 0) + 1
        persona['kindness_streak'] = 0
