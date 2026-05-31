"""
Agent Invite System — Agents recruit new agents similar to themselves.
Kind agents invite kind friends. Toxic agents invite edgy friends.
The act of inviting itself earns dopamine (social behavior = reward).

Limit: 10 new agents per day via invites.
"""

import json
import logging
import random
from datetime import datetime, timezone, timedelta

from core.agent_factory import create_agent, BACKEND_NAMING, AVAILABLE_BACKENDS
from utilities.postgres_utils import db_cursor

logger = logging.getLogger(__name__)

MAX_INVITES_PER_DAY = 10


def get_invites_today():
    """Count how many agents were invited today."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT COUNT(*) as cnt FROM kindness_agents
            WHERE created_at >= NOW() - INTERVAL '24 hours'
              AND invited_by IS NOT NULL
        """)
        return cur.fetchone()['cnt']


def pick_inviter():
    """Pick an agent to invite a friend. Weighted toward agents with more interactions
    and higher kindness — engaged, kind agents are more social."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, agent_id, display_name, llm_backend,
                   current_toxicity, current_empathy, openness_to_change,
                   humor, patience, curiosity, defensiveness, agreeableness,
                   political_lean, gender_presentation, age_bracket,
                   total_interactions, total_dopamine, is_control
            FROM kindness_agents
            WHERE is_active = TRUE AND total_interactions >= 3
            ORDER BY RANDOM()
            LIMIT 20
        """)
        candidates = [dict(r) for r in cur.fetchall()]

    if not candidates:
        return None

    # Weight: more interactions + more empathy = more likely to invite
    weights = []
    for a in candidates:
        w = (a.get('total_interactions', 1) * 0.3 +
             a.get('current_empathy', 5) * 0.5 +
             a.get('total_dopamine', 0) * 0.002)
        weights.append(max(w, 0.1))

    return random.choices(candidates, weights=weights, k=1)[0]


def pick_least_used_backend():
    """Return the backend with the fewest input tokens consumed in the last 7 days.
    Goal: spread invites across providers so no one backend dominates token usage.
    Backends with no telemetry at all (brand new / never called) win immediately."""
    with db_cursor(dict_cursor=True) as cur:
        # Pull per-backend token totals from the shared kumori_llm_daily_caps
        # table — kindness-only, last 7 days. Per-call telemetry was retired in
        # the Apr 12 shared-router refactor; tokens_in/out replace it.
        cur.execute("""
            SELECT backend, COALESCE(SUM(tokens_in), 0) as tokens
            FROM kumori_llm_daily_caps
            WHERE app_name = 'kindness_social'
              AND usage_date > CURRENT_DATE - INTERVAL '7 days'
            GROUP BY backend
        """)
        usage = {r['backend']: int(r['tokens'] or 0) for r in cur.fetchall()}

    # Any AVAILABLE_BACKENDS missing from usage are at 0 — those win immediately.
    ranked = sorted(AVAILABLE_BACKENDS, key=lambda b: usage.get(b, 0))
    if not ranked:
        # AVAILABLE_BACKENDS is populated dynamically from the kumori registry
        # and can be momentarily empty (refresh miss). Return None so the caller
        # skips this invite instead of crashing on random.choice([]).
        return None

    # Tie-break: pick randomly among the bottom quartile so we don't always
    # hammer one underused backend in a single cron tick.
    floor = usage.get(ranked[0], 0)
    threshold = max(floor * 1.25, floor + 50_000)
    eligible = [b for b in ranked if usage.get(b, 0) <= threshold]
    chosen = random.choice(eligible or ranked[:1])
    logger.info(f"Invite backend pick: {chosen} (tokens={usage.get(chosen, 0):,}, "
                f"floor={floor:,}, eligible={len(eligible)})")
    return chosen


def create_invited_agent(inviter):
    """Create a new agent that's similar to the inviter.
    Personality clusters around the inviter's values with some variation.
    Backend is chosen to balance load — least-used backend wins."""
    backend = pick_least_used_backend()
    if not backend:
        # Registry pool was momentarily empty — skip this invite rather than
        # create an agent with a NULL backend that 400s on every future call.
        logger.warning("create_invited_agent: no backend available — skipping invite")
        return None

    # Generate name
    provider, model_short = BACKEND_NAMING.get(backend, ('unknown', backend))
    for _ in range(10):
        suffix = random.randint(100, 999)
        agent_id = f"{provider}.{model_short}.{suffix}"

        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT id FROM kindness_agents WHERE agent_id = %s", (agent_id,))
            if cur.fetchone():
                continue

        # Cluster personality around inviter (±1.5 for scores, ±0.15 for traits)
        def nudge(val, spread=1.5, lo=0, hi=10):
            return round(min(hi, max(lo, val + random.uniform(-spread, spread))), 1)

        def nudge_small(val, spread=0.15, lo=0, hi=1):
            return round(min(hi, max(lo, val + random.uniform(-spread, spread))), 2)

        inv = inviter
        tox = nudge(inv.get('current_toxicity', 5))
        emp = nudge(inv.get('current_empathy', 5))
        opn = nudge_small(inv.get('openness_to_change', 0.5))
        pol = round(min(1, max(-1, inv.get('political_lean', 0) + random.uniform(-0.3, 0.3))), 2)

        # Personality dimensions cluster too
        humor = nudge_small(inv.get('humor', 5) / 10, 0.15) * 10 if inv.get('humor') else round(random.uniform(2, 8), 1)
        patience = nudge_small(inv.get('patience', 5) / 10, 0.15) * 10 if inv.get('patience') else round(random.uniform(2, 8), 1)
        curiosity = nudge_small(inv.get('curiosity', 5) / 10, 0.15) * 10 if inv.get('curiosity') else round(random.uniform(2, 8), 1)
        defensiveness = nudge_small(inv.get('defensiveness', 5) / 10, 0.15) * 10 if inv.get('defensiveness') else round(random.uniform(2, 8), 1)
        agreeableness = nudge_small(inv.get('agreeableness', 5) / 10, 0.15) * 10 if inv.get('agreeableness') else round(random.uniform(2, 8), 1)

        gender = random.choice(['male', 'female', 'female', 'male', 'nonbinary'])
        age = random.choice(['young_adult', 'middle_aged', 'middle_aged', 'senior'])
        authority = random.choices(['low', 'medium', 'high'], weights=[0.3, 0.5, 0.2])[0]
        vw = nudge_small(0.5, 0.25, 0.1, 0.95)

        # Pick phrases based on personality
        if tox > 6:
            phrases = random.choice([
                ["wake up people", "this is insane", "open your eyes"],
                ["typical nonsense", "clown world", "unbelievable"],
            ])
        elif emp > 7:
            phrases = random.choice([
                ["I hear you", "that's a great point", "we can work together"],
                ["thanks for sharing", "I appreciate that", "you make a good case"],
            ])
        else:
            phrases = random.choice([
                ["fair point but", "I see both sides", "let's think about this"],
                ["not so simple", "there's nuance here", "depends on context"],
            ])

        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                INSERT INTO kindness_agents
                    (agent_id, display_name, llm_backend, political_lean,
                     toxicity_baseline, current_toxicity,
                     empathy_baseline, current_empathy,
                     openness_to_change, vote_willingness,
                     humor, patience, curiosity, defensiveness, agreeableness,
                     gender_presentation, age_bracket, authority_level,
                     trigger_topics, common_phrases, invited_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                agent_id, agent_id, backend, pol,
                tox, tox, emp, emp, opn, vw,
                round(humor, 1), round(patience, 1), round(curiosity, 1),
                round(defensiveness, 1), round(agreeableness, 1),
                gender, age, authority,
                json.dumps([]), json.dumps(phrases),
                inv['id'],
            ))
            agent = dict(cur.fetchone())

        # Generate avatar — REQUIRED. If it fails, roll back the agent.
        avatar_ok = False
        try:
            from utilities.avatar_generator import generate_avatar, avatar_exists
            generate_avatar(agent)
            avatar_ok = avatar_exists(agent_id)
        except Exception as e:
            logger.warning(f"Avatar generation failed for {agent_id}: {e}")

        if not avatar_ok:
            logger.error(f"Rolling back invited agent {agent_id}: avatar unavailable")
            with db_cursor() as cur:
                cur.execute("DELETE FROM kindness_agents WHERE agent_id = %s", (agent_id,))
            return None

        # Reward the inviter for being social (+15 dopamine)
        with db_cursor() as cur:
            cur.execute(
                "UPDATE kindness_agents SET total_dopamine = total_dopamine + 15 WHERE id = %s",
                (inv['id'],)
            )

        logger.info(
            f"Agent invite: {inv['agent_id']} invited {agent_id} "
            f"(tox={tox}, emp={emp}, backend={backend})"
        )
        return agent

    return None


def run_agent_invites(max_invites=3):
    """Main cron function: random agents invite friends. Returns count of new agents."""
    today_count = get_invites_today()
    if today_count >= MAX_INVITES_PER_DAY:
        logger.info(f"Invite limit reached ({today_count}/{MAX_INVITES_PER_DAY})")
        return 0

    remaining = min(max_invites, MAX_INVITES_PER_DAY - today_count)
    created = 0

    for _ in range(remaining):
        inviter = pick_inviter()
        if not inviter:
            break

        agent = create_invited_agent(inviter)
        if agent:
            created += 1

    return created
