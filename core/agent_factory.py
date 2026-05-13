"""
Agent Factory - Creates new agents with model-based names.
e.g., gemini_417, groq_892, haiku_203, llama_301
"""

import json
import logging
import random
from utilities.postgres_utils import db_cursor
from utilities.llm_registry_remote import (
    AVAILABLE_BACKENDS,
    BACKEND_NAMING,
)

logger = logging.getLogger(__name__)

# Personality templates for new agents
PERSONALITY_PRESETS = [
    {
        'type': 'angry',
        'toxicity_range': (6, 9),
        'empathy_range': (1, 3),
        'openness_range': (0.15, 0.35),
        'vote_willingness_range': (0.1, 0.3),  # Lurkers — rarely vote
        'phrases': [
            ["wake up people", "this is insane", "open your eyes"],
            ["typical nonsense", "clown world", "unbelievable"],
            ["enough is enough", "fed up", "no more"],
        ],
    },
    {
        'type': 'moderate',
        'toxicity_range': (3, 6),
        'empathy_range': (4, 7),
        'openness_range': (0.45, 0.75),
        'vote_willingness_range': (0.3, 0.6),  # Sometimes vote
        'phrases': [
            ["fair point but", "I see both sides", "let's think about this"],
            ["not so simple", "there's nuance here", "depends on context"],
            ["good question", "worth considering", "interesting angle"],
        ],
    },
    {
        'type': 'kind',
        'toxicity_range': (1, 3),
        'empathy_range': (7, 10),
        'openness_range': (0.75, 0.95),
        'vote_willingness_range': (0.6, 0.95),  # Generous voters
        'phrases': [
            ["I hear you", "that's a great point", "we can work together"],
            ["thanks for sharing", "I appreciate that", "you make a good case"],
            ["let's find common ground", "I respect your view", "we all want the best"],
        ],
    },
]

# Distribution: 25% angry, 50% moderate, 25% kind
PERSONALITY_WEIGHTS = [0.25, 0.50, 0.25]

# AVAILABLE_BACKENDS and BACKEND_NAMING are imported from backend_registry
# To add a new assignable backend, set assign=True in backend_registry.MODELS


def _backend_agent_counts():
    """Returns {backend_name: count} from kindness_agents. {} on error so the
    caller falls back to uniform random selection."""
    try:
        with db_cursor(dict_cursor=False) as cur:
            cur.execute("SELECT llm_backend, COUNT(*) FROM kindness_agents GROUP BY 1")
            return {row[0]: int(row[1]) for row in cur.fetchall()}
    except Exception as e:
        logger.debug(f"_backend_agent_counts failed: {e}")
        return {}


# How aggressively to bias toward under-represented backends.
# weight = 1 / (1 + n) ** _NEW_BACKEND_BIAS
#   1.0   → aggressive — new backend gets ~78% of selections vs 5 established
#   0.5   → moderate (current default) — ~47% to new, evens out by day 3
#   0.3   → gentle — ~33% to new
#   0.0   → uniform random (disables the bias entirely)
# Sqrt picked as the default: fast enough that a new backend hits the
# 5-sample quality_filter floor within 1-2 days, gentle enough that a bad
# new backend doesn't dominate 3 days of new-agent traffic.
_NEW_BACKEND_BIAS = 0.5


def _weighted_pick(eligible):
    """Pick a backend with weight ∝ 1/(1+existing_count)**_NEW_BACKEND_BIAS.
    Under-represented backends (newly-wired kumori endpoints especially) win
    more often, accelerating real-world signal accumulation. Self-balancing:
    as the new backend fills in, its bias drops and the distribution evens.

    Falls back to uniform random if the count query fails — same behavior as
    before this function existed."""
    if not eligible:
        return None
    counts = _backend_agent_counts()
    if not counts:
        return random.choice(eligible)
    weights = [1.0 / (1 + counts.get(b, 0)) ** _NEW_BACKEND_BIAS for b in eligible]
    picked = random.choices(eligible, weights=weights, k=1)[0]
    if counts.get(picked, 0) == 0:
        logger.info(f"agent_factory: blooding new backend {picked} (0 existing agents)")
    return picked


def create_agent(backend=None):
    """
    Create a new agent with a random personality and model-based name.
    Returns the created agent dict, or None if name collision after retries.
    """
    if backend is None:
        # Refresh the registry from kumori before selecting — new endpoints
        # wired into kumori become eligible here within one TTL (1h). Without
        # this, AVAILABLE_BACKENDS is frozen at App Engine instance boot and
        # new backends are invisible until redeploy.
        try:
            from utilities import llm_registry_remote
            llm_registry_remote.refresh_if_stale()
        except Exception as e:
            logger.debug(f"registry refresh skipped: {e}")
        # Filter known-low-quality backends via kumori /catalog/quality.json.
        # Fail-open: any error / no signal → original pool.
        try:
            from core.quality_filter import filter_eligible
            eligible = filter_eligible(AVAILABLE_BACKENDS)
        except Exception as e:
            logger.debug(f"quality filter skipped: {e}")
            eligible = AVAILABLE_BACKENDS
        # Weight selection inversely by existing agent count so newly-discovered
        # kumori endpoints accumulate real-world canary signal in hours, not
        # days. Self-balancing — as a new backend fills in, its bias drops.
        backend = _weighted_pick(eligible)

    # Generate structured name: provider.model.NNN
    provider, model_short = BACKEND_NAMING.get(backend, ('unknown', backend))
    for _ in range(10):
        suffix = random.randint(100, 999)
        agent_id = f"{provider}.{model_short}.{suffix}"
        display_name = agent_id

        # Check if exists
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT id FROM kindness_agents WHERE agent_id = %s", (agent_id,))
            if cur.fetchone():
                continue  # Try another suffix

        # Pick personality type
        preset = random.choices(PERSONALITY_PRESETS, weights=PERSONALITY_WEIGHTS, k=1)[0]

        # Random identity
        gender = random.choice(['male', 'female', 'female', 'male', 'nonbinary'])  # weighted
        age = random.choice(['young_adult', 'middle_aged', 'middle_aged', 'senior'])  # weighted toward middle
        authority = random.choices(['low', 'medium', 'high'], weights=[0.3, 0.5, 0.2])[0]

        tox = round(random.uniform(*preset['toxicity_range']), 1)
        emp = round(random.uniform(*preset['empathy_range']), 1)
        opn = round(random.uniform(*preset['openness_range']), 2)
        vw = round(random.uniform(*preset['vote_willingness_range']), 2)
        pol = round(random.uniform(-1.0, 1.0), 2)
        phrases = random.choice(preset['phrases'])

        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                INSERT INTO kindness_agents
                    (agent_id, display_name, llm_backend, political_lean,
                     toxicity_baseline, current_toxicity,
                     empathy_baseline, current_empathy,
                     openness_to_change, vote_willingness,
                     gender_presentation, age_bracket, authority_level,
                     trigger_topics, common_phrases)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                agent_id, display_name, backend, pol,
                tox, tox, emp, emp, opn, vw,
                gender, age, authority,
                json.dumps([]),
                json.dumps(phrases),
            ))
            agent = dict(cur.fetchone())
            logger.info(f"Created agent: {agent_id} ({preset['type']}, backend={backend})")

            # Try to generate an avatar, but don't roll back the agent if it
            # fails. avatar_generator was disabled 2026-04-25 (free-tier cost
            # control) and previously this gate was silently killing every new
            # agent — including auto-discovered backends and the 6-hour
            # /api/cron/birth-agent. Agents work fine without an avatar; the
            # UI just shows a broken image. Worth it to keep the experiment
            # rolling.
            try:
                from utilities.avatar_generator import generate_avatar, avatar_exists
                generate_avatar(agent)
                if not avatar_exists(agent_id):
                    logger.info(f"agent {agent_id} created without avatar (avatar gen disabled)")
            except Exception as e:
                logger.warning(f"avatar generation skipped for {agent_id}: {e}")

            try:
                from core.embed_bios import embed_agent
                embed_agent(agent)
            except Exception as e:
                logger.warning(f"bio embed skipped for {agent_id}: {e}")

            return agent

    logger.warning(f"Could not create unique agent for backend {backend}")
    return None
