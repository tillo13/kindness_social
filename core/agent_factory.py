"""
Agent Factory - Creates new agents with model-based names.
e.g., gemini_417, grok_892, haiku_203, llama_301
"""

import json
import logging
import random
from utilities.postgres_utils import db_cursor

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

AVAILABLE_BACKENDS = ['gemini', 'grok', 'deepseek', 'gpt4o_mini', 'haiku', 'sonnet', 'local']


def create_agent(backend=None):
    """
    Create a new agent with a random personality and model-based name.
    Returns the created agent dict, or None if name collision after retries.
    """
    if backend is None:
        backend = random.choice(AVAILABLE_BACKENDS)

    # Generate unique name: backend_XXX
    for _ in range(10):
        suffix = random.randint(100, 999)
        agent_id = f"{backend}_{suffix}"
        display_name = f"{backend}_{suffix}"

        # Check if exists
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT id FROM kindness_agents WHERE agent_id = %s", (agent_id,))
            if cur.fetchone():
                continue  # Try another suffix

        # Pick personality type
        preset = random.choices(PERSONALITY_PRESETS, weights=PERSONALITY_WEIGHTS, k=1)[0]

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
                     trigger_topics, common_phrases)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                agent_id, display_name, backend, pol,
                tox, tox, emp, emp, opn, vw,
                json.dumps([]),
                json.dumps(phrases),
            ))
            agent = dict(cur.fetchone())
            logger.info(f"Created agent: {agent_id} ({preset['type']}, backend={backend})")
            return agent

    logger.warning(f"Could not create unique agent for backend {backend}")
    return None
