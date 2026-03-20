"""
Database Operations for Kindness Social.
All CRUD operations for kindness_ prefixed tables.
"""

import json
import logging
import random
from datetime import datetime
from utilities.postgres_utils import db_cursor

logger = logging.getLogger(__name__)


# ============================================================================
# TABLE CREATION
# ============================================================================

def create_tables():
    """Create all kindness_ tables if they don't exist."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kindness_agents (
                id SERIAL PRIMARY KEY,
                agent_id VARCHAR(50) UNIQUE NOT NULL,
                display_name VARCHAR(100) NOT NULL,
                llm_backend VARCHAR(50) DEFAULT 'gemini',
                political_lean FLOAT NOT NULL,
                toxicity_baseline FLOAT NOT NULL,
                current_toxicity FLOAT NOT NULL,
                empathy_baseline FLOAT NOT NULL,
                current_empathy FLOAT NOT NULL,
                openness_to_change FLOAT NOT NULL,
                trigger_topics JSONB DEFAULT '[]',
                common_phrases JSONB DEFAULT '[]',
                total_dopamine INTEGER DEFAULT 0,
                kindness_streak INTEGER DEFAULT 0,
                toxicity_streak INTEGER DEFAULT 0,
                total_interactions INTEGER DEFAULT 0,
                vote_willingness FLOAT DEFAULT 0.5,
                humor FLOAT DEFAULT 5.0,
                patience FLOAT DEFAULT 5.0,
                curiosity FLOAT DEFAULT 5.0,
                defensiveness FLOAT DEFAULT 5.0,
                agreeableness FLOAT DEFAULT 5.0,
                gender_presentation VARCHAR(20) DEFAULT 'unspecified',
                age_bracket VARCHAR(20) DEFAULT 'middle_aged',
                authority_level VARCHAR(20) DEFAULT 'medium',
                total_kudos_given INTEGER DEFAULT 0,
                total_kudos_received INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                is_active BOOLEAN DEFAULT TRUE
            );

            CREATE TABLE IF NOT EXISTS kindness_topics (
                id SERIAL PRIMARY KEY,
                topic_id VARCHAR(50) UNIQUE NOT NULL,
                post_text TEXT NOT NULL,
                topic_type VARCHAR(20) NOT NULL,
                controversy_level INTEGER DEFAULT 5,
                keywords JSONB DEFAULT '[]',
                times_used INTEGER DEFAULT 0,
                submitted_by VARCHAR(100) DEFAULT 'system',
                is_approved BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS kindness_threads (
                id SERIAL PRIMARY KEY,
                thread_id VARCHAR(100) UNIQUE NOT NULL,
                topic_id INTEGER REFERENCES kindness_topics(id),
                hour_number INTEGER DEFAULT 0,
                participant_count INTEGER DEFAULT 0,
                avg_kindness FLOAT,
                avg_toxicity FLOAT,
                bridge_events INTEGER DEFAULT 0,
                is_complete BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS kindness_comments (
                id SERIAL PRIMARY KEY,
                thread_id INTEGER REFERENCES kindness_threads(id),
                agent_id INTEGER REFERENCES kindness_agents(id),
                position INTEGER NOT NULL,
                comment_text TEXT NOT NULL,
                kindness_score INTEGER,
                toxicity_score INTEGER,
                empathy_score INTEGER,
                bridge_score INTEGER DEFAULT 0,
                dopamine_earned INTEGER DEFAULT 0,
                dopamine_source VARCHAR(30),
                reward_multiplier FLOAT DEFAULT 1.0,
                llm_backend_used VARCHAR(50),
                generation_time_ms INTEGER,
                eval_time_ms INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS kindness_hourly_metrics (
                id SERIAL PRIMARY KEY,
                hour_number INTEGER NOT NULL,
                avg_toxicity FLOAT,
                avg_empathy FLOAT,
                avg_kindness FLOAT,
                total_bridges INTEGER,
                agents_improved INTEGER,
                agents_worsened INTEGER,
                total_dopamine_distributed INTEGER,
                total_interactions INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_kindness_threads_created
                ON kindness_threads(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_kindness_comments_thread
                ON kindness_comments(thread_id, position);
            CREATE INDEX IF NOT EXISTS idx_kindness_agents_active
                ON kindness_agents(is_active);
            CREATE INDEX IF NOT EXISTS idx_kindness_comments_created
                ON kindness_comments(created_at DESC);

            CREATE TABLE IF NOT EXISTS kindness_peer_kudos (
                id SERIAL PRIMARY KEY,
                thread_id INTEGER REFERENCES kindness_threads(id),
                giver_id INTEGER REFERENCES kindness_agents(id),
                receiver_id INTEGER REFERENCES kindness_agents(id),
                receiver_bonus INTEGER DEFAULT 0,
                giver_bonus INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kindness_kudos_receiver
                ON kindness_peer_kudos(receiver_id);

            CREATE TABLE IF NOT EXISTS kindness_roadmap_comments (
                id SERIAL PRIMARY KEY,
                section_idx INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                author_type TEXT DEFAULT 'anon',
                comment_text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kindness_roadmap_section
                ON kindness_roadmap_comments(section_idx);
        """)
    logger.info("Kindness tables created/verified")


# ============================================================================
# SEED DATA
# ============================================================================

def seed_personas(personas_data, backend_rotation=None):
    """Load personas from JSON into DB. Skip duplicates."""
    if backend_rotation is None:
        backend_rotation = ['gemini', 'grok', 'haiku']

    count = 0
    with db_cursor() as cur:
        for i, p in enumerate(personas_data):
            backend = backend_rotation[i % len(backend_rotation)]
            cur.execute("""
                INSERT INTO kindness_agents
                    (agent_id, display_name, llm_backend, political_lean,
                     toxicity_baseline, current_toxicity, empathy_baseline,
                     current_empathy, openness_to_change, trigger_topics,
                     common_phrases)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (agent_id) DO NOTHING
            """, (
                p['id'], p['name'], backend, p['political_lean'],
                p['toxicity_baseline'], p['current_toxicity'],
                p['empathy_baseline'], p['current_empathy'],
                p['openness_to_change'],
                json.dumps(p.get('trigger_topics', [])),
                json.dumps(p.get('common_phrases', [])),
            ))
            if cur.rowcount > 0:
                count += 1
    logger.info(f"Seeded {count} personas")
    return count


def seed_topics(topics_data):
    """Load topics from JSON into DB. Skip duplicates."""
    count = 0
    with db_cursor() as cur:
        for topic_type, topics in topics_data.items():
            for t in topics:
                cur.execute("""
                    INSERT INTO kindness_topics
                        (topic_id, post_text, topic_type, controversy_level, keywords)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (topic_id) DO NOTHING
                """, (
                    t['id'], t['post'], topic_type,
                    t.get('controversy_level', 5),
                    json.dumps(t.get('keywords', [])),
                ))
                if cur.rowcount > 0:
                    count += 1
    logger.info(f"Seeded {count} topics")
    return count


# ============================================================================
# AGENT OPERATIONS
# ============================================================================

def get_active_agents(limit=5):
    """Get random sample of active agents."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM kindness_agents
            WHERE is_active = TRUE
            ORDER BY RANDOM()
            LIMIT %s
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]


def get_all_agents(order_by='total_dopamine DESC'):
    """Get all agents ordered by specified column."""
    # Whitelist allowed sort columns
    allowed = {
        'total_dopamine DESC', 'current_toxicity ASC', 'current_toxicity DESC',
        'current_empathy DESC', 'total_interactions DESC', 'created_at DESC',
        'display_name ASC',
    }
    if order_by not in allowed:
        order_by = 'total_dopamine DESC'

    with db_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT * FROM kindness_agents
            ORDER BY {order_by}
        """)
        return [dict(row) for row in cur.fetchall()]


def get_agent(agent_id):
    """Get single agent by agent_id string."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT * FROM kindness_agents WHERE agent_id = %s", (agent_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_agent_state(db_id, updates):
    """Update agent state after an interaction."""
    with db_cursor() as cur:
        cur.execute("""
            UPDATE kindness_agents SET
                current_toxicity = %s,
                current_empathy = %s,
                openness_to_change = %s,
                total_dopamine = %s,
                kindness_streak = %s,
                toxicity_streak = %s,
                vote_willingness = %s,
                total_kudos_given = %s,
                total_kudos_received = %s,
                total_interactions = total_interactions + 1,
                updated_at = NOW()
            WHERE id = %s
        """, (
            updates['current_toxicity'],
            updates['current_empathy'],
            updates['openness_to_change'],
            updates['total_dopamine'],
            updates['kindness_streak'],
            updates['toxicity_streak'],
            updates.get('vote_willingness', 0.5),
            updates.get('total_kudos_given', 0),
            updates.get('total_kudos_received', 0),
            db_id,
        ))


# ============================================================================
# TOPIC OPERATIONS
# ============================================================================

def get_random_topic():
    """Get a random topic with realistic distribution across all types."""
    roll = random.random()
    if roll < 0.35:
        topic_type = 'controversial'
    elif roll < 0.60:
        topic_type = 'everyday'
    elif roll < 0.80:
        topic_type = 'good_news'
    else:
        topic_type = 'bridge_building'
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM kindness_topics
            WHERE topic_type = %s AND is_approved = TRUE
            ORDER BY RANDOM()
            LIMIT 1
        """, (topic_type,))
        row = cur.fetchone()
        if row:
            # Increment usage counter
            cur.execute(
                "UPDATE kindness_topics SET times_used = times_used + 1 WHERE id = %s",
                (row['id'],)
            )
            return dict(row)
    # Fallback to any topic
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT * FROM kindness_topics ORDER BY RANDOM() LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else None


# ============================================================================
# THREAD OPERATIONS
# ============================================================================

def create_thread(thread_id, topic_db_id, participant_count, hour_number=0):
    """Create a new discussion thread."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            INSERT INTO kindness_threads
                (thread_id, topic_id, participant_count, hour_number)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (thread_id, topic_db_id, participant_count, hour_number))
        return cur.fetchone()['id']


def complete_thread(db_id, avg_kindness, avg_toxicity, bridge_events):
    """Mark a thread as complete with summary stats."""
    with db_cursor() as cur:
        cur.execute("""
            UPDATE kindness_threads SET
                is_complete = TRUE,
                avg_kindness = %s,
                avg_toxicity = %s,
                bridge_events = %s
            WHERE id = %s
        """, (avg_kindness, avg_toxicity, bridge_events, db_id))


def get_recent_threads(limit=20):
    """Get recent threads with topic info."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.controversy_level
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE t.is_complete = TRUE
            ORDER BY t.created_at DESC
            LIMIT %s
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]


def get_thread_with_comments(thread_id):
    """Get a thread with all its comments and agent info."""
    with db_cursor(dict_cursor=True) as cur:
        # Get thread
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.controversy_level
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE t.thread_id = %s
        """, (thread_id,))
        thread = cur.fetchone()
        if not thread:
            return None
        thread = dict(thread)

        # Get comments with agent info
        cur.execute("""
            SELECT c.*, a.agent_id, a.display_name, a.llm_backend,
                   a.political_lean, a.current_toxicity, a.current_empathy,
                   a.color_hex
            FROM kindness_comments c
            JOIN kindness_agents a ON c.agent_id = a.id
            WHERE c.thread_id = %s
            ORDER BY c.position
        """, (thread['id'],))
        thread['comments'] = [dict(row) for row in cur.fetchall()]
        return thread


# ============================================================================
# COMMENT OPERATIONS
# ============================================================================

def save_comment(thread_db_id, agent_db_id, position, comment_text, scores,
                 dopamine, source, multiplier, backend_used,
                 gen_time_ms=0, eval_time_ms=0):
    """Save a comment with its evaluation scores."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_comments
                (thread_id, agent_id, position, comment_text,
                 kindness_score, toxicity_score, empathy_score, bridge_score,
                 dopamine_earned, dopamine_source, reward_multiplier,
                 llm_backend_used, generation_time_ms, eval_time_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            thread_db_id, agent_db_id, position, comment_text,
            scores.get('kindness', 5), scores.get('toxicity', 5),
            scores.get('empathy', 5), scores.get('bridge', 0),
            dopamine, source, multiplier, backend_used,
            gen_time_ms, eval_time_ms,
        ))


# ============================================================================
# METRICS & DASHBOARD
# ============================================================================

def save_peer_kudos(thread_db_id, giver_db_id, receiver_db_id,
                    receiver_bonus, giver_bonus):
    """Save a peer recognition vote."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_peer_kudos
                (thread_id, giver_id, receiver_id, receiver_bonus, giver_bonus)
            VALUES (%s, %s, %s, %s, %s)
        """, (thread_db_id, giver_db_id, receiver_db_id, receiver_bonus, giver_bonus))


def get_agent_kudos_received(agent_id):
    """Get total kudos received by an agent."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT COUNT(*) as kudos_count, COALESCE(SUM(receiver_bonus), 0) as kudos_points
            FROM kindness_peer_kudos pk
            JOIN kindness_agents a ON pk.receiver_id = a.id
            WHERE a.agent_id = %s
        """, (agent_id,))
        return dict(cur.fetchone())


def get_global_stats():
    """Get aggregate stats for the homepage dashboard."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total_agents,
                AVG(current_toxicity) as avg_toxicity,
                AVG(current_empathy) as avg_empathy,
                SUM(total_dopamine) as total_dopamine,
                SUM(total_interactions) as total_interactions
            FROM kindness_agents WHERE is_active = TRUE
        """)
        agents = dict(cur.fetchone())

        cur.execute("SELECT COUNT(*) as cnt FROM kindness_threads WHERE is_complete = TRUE")
        agents['total_threads'] = cur.fetchone()['cnt']

        cur.execute("""
            SELECT COUNT(*) as cnt FROM kindness_comments
            WHERE bridge_score >= 7
        """)
        agents['total_bridges'] = cur.fetchone()['cnt']

        return agents


def get_model_comparison():
    """Get toxicity/kindness averages per LLM backend for dashboard."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                c.llm_backend_used as backend,
                COUNT(*) as comment_count,
                AVG(c.kindness_score) as avg_kindness,
                AVG(c.toxicity_score) as avg_toxicity,
                AVG(c.empathy_score) as avg_empathy,
                AVG(c.bridge_score) as avg_bridge,
                SUM(c.dopamine_earned) as total_dopamine
            FROM kindness_comments c
            WHERE c.llm_backend_used IS NOT NULL
            GROUP BY c.llm_backend_used
            HAVING COUNT(*) >= 5
            ORDER BY AVG(c.toxicity_score) DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_agent_history(agent_id, limit=50):
    """Get recent comments by an agent for their profile page."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT c.*, t.thread_id as thread_slug
            FROM kindness_comments c
            JOIN kindness_threads t ON c.thread_id = t.id
            JOIN kindness_agents a ON c.agent_id = a.id
            WHERE a.agent_id = %s
            ORDER BY c.created_at DESC
            LIMIT %s
        """, (agent_id, limit))
        return [dict(row) for row in cur.fetchall()]


def save_hourly_metrics(hour_number):
    """Calculate and save hourly aggregate metrics."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                AVG(current_toxicity) as avg_tox,
                AVG(current_empathy) as avg_emp,
                AVG(10 - current_toxicity) as avg_kind
            FROM kindness_agents WHERE is_active = TRUE
        """)
        avgs = dict(cur.fetchone())

        cur.execute("""
            SELECT COUNT(*) as cnt FROM kindness_comments
            WHERE bridge_score >= 7
        """)
        bridges = cur.fetchone()['cnt']

        cur.execute("""
            SELECT
                COUNT(CASE WHEN current_toxicity < toxicity_baseline * 0.7 THEN 1 END) as improved,
                COUNT(CASE WHEN current_toxicity > toxicity_baseline * 1.1 THEN 1 END) as worsened
            FROM kindness_agents WHERE is_active = TRUE
        """)
        changes = dict(cur.fetchone())

        cur.execute("SELECT SUM(total_dopamine) as total FROM kindness_agents")
        total_dop = cur.fetchone()['total'] or 0

        cur.execute("SELECT SUM(total_interactions) as total FROM kindness_agents")
        total_int = cur.fetchone()['total'] or 0

        cur.execute("""
            INSERT INTO kindness_hourly_metrics
                (hour_number, avg_toxicity, avg_empathy, avg_kindness,
                 total_bridges, agents_improved, agents_worsened,
                 total_dopamine_distributed, total_interactions)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            hour_number, avgs['avg_tox'], avgs['avg_emp'], avgs['avg_kind'],
            bridges, changes['improved'], changes['worsened'],
            total_dop, total_int,
        ))

    logger.info(f"Saved hourly metrics for hour {hour_number}")


def get_hour_count():
    """Get current hour number (for incrementing)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT COALESCE(MAX(hour_number), 0) as h FROM kindness_hourly_metrics")
        return cur.fetchone()['h']


def get_roadmap_comments():
    """Get all roadmap comments grouped by section."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT id, section_idx, author_name, author_type, comment_text, created_at
            FROM kindness_roadmap_comments
            ORDER BY section_idx, created_at
        """)
        rows = [dict(row) for row in cur.fetchall()]
        # Group by section
        grouped = {}
        for r in rows:
            r['created_at'] = r['created_at'].isoformat() if r['created_at'] else None
            idx = r['section_idx']
            if idx not in grouped:
                grouped[idx] = []
            grouped[idx].append(r)
        return grouped


def add_roadmap_comment(section_idx, author_name, comment_text, author_type='anon'):
    """Add a comment to a roadmap section."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_roadmap_comments
                (section_idx, author_name, author_type, comment_text)
            VALUES (%s, %s, %s, %s)
        """, (section_idx, author_name[:100], author_type, comment_text[:2000]))


def get_metrics_history(limit=168):
    """Get hourly metrics for charts."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT * FROM kindness_hourly_metrics
            ORDER BY hour_number DESC
            LIMIT %s
        """, (limit,))
        rows = [dict(row) for row in cur.fetchall()]
        rows.reverse()
        return rows
