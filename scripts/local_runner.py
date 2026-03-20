#!/usr/bin/env python3
"""
Local Runner - Generate discussion threads from your local machine.
Uses rog_gateway for local LLM and writes results to Cloud SQL.
Can also use any other backend (gemini, groq, claude).

Usage:
    python scripts/local_runner.py                    # Run 1 thread
    python scripts/local_runner.py --count 5          # Run 5 threads
    python scripts/local_runner.py --backend local    # Force local LLM
    python scripts/local_runner.py --birth gemini     # Birth a new agent
"""

import argparse
import json
import os
import sys

# Add parent dir to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
except ImportError:
    pass

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s'
)
logger = logging.getLogger('local_runner')


def run_threads(count=1, backend=None):
    """Run N discussion threads."""
    from core import db_ops
    from core.simulator import run_thread, DEFAULT_CONFIG

    # Ensure tables exist
    db_ops.create_tables()

    # Check we have agents
    agents = db_ops.get_active_agents(limit=1)
    if not agents:
        logger.info("No agents found, seeding from personas.json...")
        seed_data()

    for i in range(count):
        logger.info(f"\n{'='*60}")
        logger.info(f"Thread {i+1}/{count}")
        logger.info(f"{'='*60}")

        config = dict(DEFAULT_CONFIG)
        result = run_thread(config)

        if 'error' in result:
            logger.error(f"Thread failed: {result['error']}")
        else:
            logger.info(f"Thread completed: {result}")

    logger.info(f"\nDone! {count} thread(s) generated.")


def seed_data():
    """Load personas and topics into DB."""
    from core import db_ops

    db_ops.create_tables()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(os.path.join(base_dir, 'personas.json')) as f:
        personas = json.load(f)
    p = db_ops.seed_personas(personas)

    with open(os.path.join(base_dir, 'topics.json')) as f:
        topics = json.load(f)
    t = db_ops.seed_topics(topics)

    logger.info(f"Seeded {p} personas, {t} topics")


def birth_agent(backend=None):
    """Create a new agent."""
    from core.agent_factory import create_agent
    agent = create_agent(backend=backend)
    if agent:
        logger.info(f"Created: {agent['agent_id']} (backend={agent['llm_backend']})")
    else:
        logger.error("Failed to create agent")


def main():
    parser = argparse.ArgumentParser(description='Kindness Social - Local Runner')
    parser.add_argument('--count', type=int, default=1, help='Number of threads to generate')
    parser.add_argument('--backend', type=str, default=None, help='Force a specific LLM backend')
    parser.add_argument('--seed', action='store_true', help='Seed personas and topics into DB')
    parser.add_argument('--birth', type=str, nargs='?', const=None, default=False,
                        help='Birth a new agent (optionally specify backend)')
    args = parser.parse_args()

    if args.seed:
        seed_data()
    elif args.birth is not False:
        birth_agent(backend=args.birth)
    else:
        run_threads(count=args.count, backend=args.backend)


if __name__ == '__main__':
    main()
