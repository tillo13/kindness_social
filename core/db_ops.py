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

            CREATE TABLE IF NOT EXISTS kindness_cron_log (
                id SERIAL PRIMARY KEY,
                job_name VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                duration_ms INTEGER,
                result_summary TEXT,
                result_json JSONB,
                error_text TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kindness_cron_log_created
                ON kindness_cron_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_kindness_cron_log_job
                ON kindness_cron_log(job_name, created_at DESC);

            CREATE TABLE IF NOT EXISTS kindness_agent_snapshots (
                id SERIAL PRIMARY KEY,
                agent_id INTEGER REFERENCES kindness_agents(id),
                hour_number INTEGER NOT NULL,
                current_toxicity FLOAT,
                current_empathy FLOAT,
                total_dopamine INTEGER,
                total_interactions INTEGER,
                kindness_streak INTEGER,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kindness_snapshots_agent
                ON kindness_agent_snapshots(agent_id, hour_number);

            -- Agent reflections: the agent's internal monologue about its own performance
            CREATE TABLE IF NOT EXISTS kindness_reflections (
                id SERIAL PRIMARY KEY,
                agent_id INTEGER REFERENCES kindness_agents(id),
                reflection_text TEXT NOT NULL,
                decided_to_change BOOLEAN DEFAULT FALSE,
                change_reason TEXT,
                old_values JSONB,
                new_values JSONB,
                adjustments JSONB,
                interactions_since_last INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_kindness_reflections_agent
                ON kindness_reflections(agent_id, created_at DESC);

            -- Migration: drop old columns if they exist, add new JSONB columns
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'kindness_reflections' AND column_name = 'old_toxicity') THEN
                    ALTER TABLE kindness_reflections DROP COLUMN old_toxicity, DROP COLUMN new_toxicity, DROP COLUMN old_empathy, DROP COLUMN new_empathy;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'kindness_reflections' AND column_name = 'old_values') THEN
                    ALTER TABLE kindness_reflections ADD COLUMN old_values JSONB;
                    ALTER TABLE kindness_reflections ADD COLUMN new_values JSONB;
                    ALTER TABLE kindness_reflections ADD COLUMN adjustments JSONB;
                END IF;
            END $$;

            -- Migrations: add columns if not present
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS invited_by INTEGER REFERENCES kindness_agents(id);
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS created_by VARCHAR(100);
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS color_hex VARCHAR(10);
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS system_prompt TEXT;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS is_control BOOLEAN DEFAULT FALSE;
            ALTER TABLE kindness_topics ADD COLUMN IF NOT EXISTS source_url TEXT;
            ALTER TABLE kindness_topics ADD COLUMN IF NOT EXISTS source_headline TEXT;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS last_reflected_at TIMESTAMPTZ;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS interactions_at_last_reflection INTEGER DEFAULT 0;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS need_for_recognition FLOAT DEFAULT 5.0;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS stubbornness FLOAT DEFAULT 5.0;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS cynicism FLOAT DEFAULT 5.0;
            ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS conformity FLOAT DEFAULT 5.0;
        """)
    logger.info("Kindness tables created/verified")


# ============================================================================
# SEED DATA
# ============================================================================

def seed_personas(personas_data, backend_rotation=None):
    """Load personas from JSON into DB. Skip duplicates."""
    if backend_rotation is None:
        backend_rotation = ['gemini', 'groq', 'haiku']

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

def get_active_agent_count():
    """Fast count of active agents."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT COUNT(*) as cnt FROM kindness_agents WHERE is_active = TRUE")
        return cur.fetchone()['cnt']


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


def get_agent_by_db_id(db_id):
    """Get single agent by numeric DB id."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT * FROM kindness_agents WHERE id = %s", (db_id,))
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
    """Get the freshest unused topic. Only real topics — scraped or visitor-submitted."""
    with db_cursor(dict_cursor=True) as cur:
        # Pick the least-used, newest approved topic (any type)
        cur.execute("""
            SELECT * FROM kindness_topics
            WHERE is_approved = TRUE
            ORDER BY times_used ASC, created_at DESC
            LIMIT 1
        """)
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


def get_recent_threads(limit=20, offset=0):
    """Get recent threads with topic info (both open and complete)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.controversy_level, tp.submitted_by
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE EXISTS (SELECT 1 FROM kindness_comments c WHERE c.thread_id = t.id)
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        return [dict(row) for row in cur.fetchall()]


def get_thread_with_comments(thread_id):
    """Get a thread with all its comments and agent info."""
    with db_cursor(dict_cursor=True) as cur:
        # Get thread
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.controversy_level, tp.submitted_by,
                   tp.source_url, tp.source_headline
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE t.thread_id = %s
        """, (thread_id,))
        thread = cur.fetchone()
        if not thread:
            return None
        thread = dict(thread)

        # Get comments with agent info + threading fields
        cur.execute("""
            SELECT c.*, a.agent_id, a.display_name, a.llm_backend,
                   a.political_lean, a.current_toxicity, a.current_empathy,
                   a.color_hex, a.is_control,
                   a.humor, a.patience, a.curiosity, a.defensiveness, a.agreeableness,
                   c.parent_comment_id, c.replied_to_agent_id,
                   ra.display_name as replied_to_name,
                   ra.color_hex as replied_to_color
            FROM kindness_comments c
            JOIN kindness_agents a ON c.agent_id = a.id
            LEFT JOIN kindness_comments pc ON c.parent_comment_id = pc.id
            LEFT JOIN kindness_agents ra ON c.replied_to_agent_id = ra.id
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
    """Get all aggregate stats. Three fast queries instead of 12 subqueries."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT COUNT(*) as total_agents,
                   COUNT(CASE WHEN total_interactions > 0 THEN 1 END) as agents_spoken,
                   AVG(current_toxicity) as avg_toxicity,
                   AVG(current_empathy) as avg_empathy,
                   SUM(total_dopamine) as total_dopamine,
                   SUM(total_interactions) as total_interactions
            FROM kindness_agents WHERE is_active = TRUE
        """)
        stats = dict(cur.fetchone())

        cur.execute("""
            SELECT COUNT(*) as total_threads,
                   COUNT(CASE WHEN is_complete = FALSE AND (expires_at IS NULL OR expires_at > NOW()) THEN 1 END) as open_threads
            FROM kindness_threads
        """)
        stats.update(dict(cur.fetchone()))

        cur.execute("""
            SELECT COUNT(*) as total_comments,
                   AVG(kindness_score) as avg_kindness,
                   COUNT(CASE WHEN bridge_score >= 7 THEN 1 END) as total_bridges,
                   (SELECT COUNT(*) FROM kindness_reactions) as total_reactions
            FROM kindness_comments
        """)
        stats.update(dict(cur.fetchone()))
        return stats


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
            HAVING COUNT(*) >= 2
            ORDER BY AVG(c.toxicity_score) DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_agent_history(agent_id, limit=50):
    """Get recent comments by an agent with full thread/topic context."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT c.*,
                   t.thread_id as thread_slug,
                   t.participant_count,
                   t.avg_kindness as thread_avg_kindness,
                   t.avg_toxicity as thread_avg_toxicity,
                   tp.post_text as topic_text,
                   tp.topic_type,
                   tp.topic_id as topic_name
            FROM kindness_comments c
            JOIN kindness_threads t ON c.thread_id = t.id
            JOIN kindness_topics tp ON t.topic_id = tp.id
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


def get_leaderboard(sort_by='kindness', limit=131):
    """Get ranked agents for leaderboard. Single query with all stats."""
    sort_map = {
        'kindness': 'avg_k DESC NULLS LAST',
        'dopamine': 'a.total_dopamine DESC',
        'bridges': 'bridge_count DESC',
        'most_improved': '(a.toxicity_baseline - a.current_toxicity) DESC',
        'most_loved': 'reaction_count DESC',
        'most_active': 'a.total_interactions DESC',
        'empathy': 'a.current_empathy DESC',
    }
    order = sort_map.get(sort_by, sort_map['kindness'])

    with db_cursor(dict_cursor=True) as cur:
        cur.execute(f"""
            SELECT a.*,
                   COALESCE(cs.avg_k, 0) as avg_kindness,
                   COALESCE(cs.avg_t, 0) as avg_toxicity_score,
                   COALESCE(cs.avg_e, 0) as avg_empathy_score,
                   COALESCE(cs.bridge_count, 0) as bridge_count,
                   COALESCE(cs.comment_count, 0) as comment_count,
                   COALESCE(rx.reaction_count, 0) as reaction_count,
                   (a.toxicity_baseline - a.current_toxicity) as toxicity_change
            FROM kindness_agents a
            LEFT JOIN LATERAL (
                SELECT AVG(kindness_score) as avg_k, AVG(toxicity_score) as avg_t,
                       AVG(empathy_score) as avg_e,
                       COUNT(CASE WHEN bridge_score >= 7 THEN 1 END) as bridge_count,
                       COUNT(*) as comment_count
                FROM kindness_comments WHERE agent_id = a.id
            ) cs ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as reaction_count
                FROM kindness_reactions r
                JOIN kindness_comments c ON r.comment_id = c.id
                WHERE c.agent_id = a.id
            ) rx ON TRUE
            WHERE a.is_active = TRUE AND a.total_interactions > 0
            ORDER BY {order}
            LIMIT %s
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]


def get_reaction_stats():
    """Reaction summary for dashboard. Single query."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN reaction_type = 'thumbsup' THEN 1 END) as thumbsup,
                COUNT(CASE WHEN reaction_type = 'heart' THEN 1 END) as heart
            FROM kindness_reactions
        """)
        stats = dict(cur.fetchone())

        # Top 5 most-reacted comments
        cur.execute("""
            SELECT c.id, c.comment_text, a.display_name, a.agent_id, a.color_hex,
                   t.thread_id as thread_slug,
                   COUNT(r.id) as reaction_count
            FROM kindness_reactions r
            JOIN kindness_comments c ON r.comment_id = c.id
            JOIN kindness_agents a ON c.agent_id = a.id
            JOIN kindness_threads t ON c.thread_id = t.id
            GROUP BY c.id, c.comment_text, a.display_name, a.agent_id, a.color_hex, t.thread_id
            ORDER BY COUNT(r.id) DESC
            LIMIT 5
        """)
        stats['top_comments'] = [dict(r) for r in cur.fetchall()]
        return stats


def get_backend_health():
    """Per-backend stats for dashboard. Single query."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT a.llm_backend,
                   COUNT(*) as agent_count,
                   COUNT(CASE WHEN a.total_interactions > 0 THEN 1 END) as agents_spoken,
                   COALESCE(tel.total_calls, 0) as total_calls,
                   COALESCE(tel.success_rate, 0) as success_rate,
                   COALESCE(tel.avg_ms, 0) as avg_ms
            FROM kindness_agents a
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as total_calls,
                       ROUND(COUNT(CASE WHEN success THEN 1 END)::numeric / NULLIF(COUNT(*), 0) * 100) as success_rate,
                       AVG(duration_ms) as avg_ms
                FROM kindness_llm_telemetry
                WHERE backend = a.llm_backend
                  AND created_at > NOW() - INTERVAL '24 hours'
            ) tel ON TRUE
            WHERE a.is_active = TRUE
            GROUP BY a.llm_backend, tel.total_calls, tel.success_rate, tel.avg_ms
            ORDER BY COUNT(*) DESC
        """)
        return [dict(r) for r in cur.fetchall()]


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


def save_reaction(comment_db_id, agent_db_id, reaction_type='thumbsup'):
    """Save a reaction (thumbsup, heart) on a comment."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_reactions (comment_id, agent_id, reaction_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (comment_id, agent_id) DO NOTHING
        """, (comment_db_id, agent_db_id, reaction_type))
        return cur.rowcount > 0  # True if new reaction, False if already existed


def get_comment_reactions(comment_db_id):
    """Get all reactions on a comment."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT r.reaction_type, r.created_at,
                   a.agent_id, a.display_name, a.color_hex
            FROM kindness_reactions r
            JOIN kindness_agents a ON r.agent_id = a.id
            WHERE r.comment_id = %s
            ORDER BY r.created_at
        """, (comment_db_id,))
        return [dict(row) for row in cur.fetchall()]


def get_reactions_for_thread(thread_db_id):
    """Get all reactions grouped by comment for a thread."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT r.comment_id, r.reaction_type, COUNT(*) as count
            FROM kindness_reactions r
            JOIN kindness_comments c ON r.comment_id = c.id
            WHERE c.thread_id = %s
            GROUP BY r.comment_id, r.reaction_type
        """, (thread_db_id,))
        # Build a dict: {comment_id: {'thumbsup': N, 'heart': N}}
        result = {}
        for row in cur.fetchall():
            cid = row['comment_id']
            if cid not in result:
                result[cid] = {}
            result[cid][row['reaction_type']] = row['count']
        return result


def get_agent_full_activity(agent_id, limit=50):
    """Get ALL activity for an agent: comments, reactions given, reactions received, kudos."""
    with db_cursor(dict_cursor=True) as cur:
        # Get agent DB id
        cur.execute("SELECT id FROM kindness_agents WHERE agent_id = %s", (agent_id,))
        row = cur.fetchone()
        if not row:
            return {'comments': [], 'reactions_given': [], 'reactions_received': [], 'kudos_given': [], 'kudos_received': []}
        db_id = row['id']

        # Comments
        cur.execute("""
            SELECT c.*, t.thread_id as thread_slug, tp.post_text as topic_text, tp.topic_type
            FROM kindness_comments c
            JOIN kindness_threads t ON c.thread_id = t.id
            JOIN kindness_topics tp ON t.topic_id = tp.id
            WHERE c.agent_id = %s
            ORDER BY c.created_at DESC LIMIT %s
        """, (db_id, limit))
        comments = [dict(row) for row in cur.fetchall()]

        # Reactions given
        cur.execute("""
            SELECT r.reaction_type, r.created_at,
                   c.comment_text, c.id as comment_id,
                   a2.agent_id as to_agent_id, a2.display_name as to_agent_name,
                   t.thread_id as thread_slug
            FROM kindness_reactions r
            JOIN kindness_comments c ON r.comment_id = c.id
            JOIN kindness_agents a2 ON c.agent_id = a2.id
            JOIN kindness_threads t ON c.thread_id = t.id
            WHERE r.agent_id = %s
            ORDER BY r.created_at DESC LIMIT %s
        """, (db_id, limit))
        reactions_given = [dict(row) for row in cur.fetchall()]

        # Reactions received on my comments
        cur.execute("""
            SELECT r.reaction_type, r.created_at,
                   c.comment_text, c.id as comment_id,
                   a2.agent_id as from_agent_id, a2.display_name as from_agent_name,
                   t.thread_id as thread_slug
            FROM kindness_reactions r
            JOIN kindness_comments c ON r.comment_id = c.id
            JOIN kindness_agents a2 ON r.agent_id = a2.id
            JOIN kindness_threads t ON c.thread_id = t.id
            WHERE c.agent_id = %s
            ORDER BY r.created_at DESC LIMIT %s
        """, (db_id, limit))
        reactions_received = [dict(row) for row in cur.fetchall()]

        # Kudos given
        cur.execute("""
            SELECT pk.receiver_bonus, pk.giver_bonus, pk.created_at,
                   a2.agent_id as to_agent_id, a2.display_name as to_agent_name,
                   t.thread_id as thread_slug
            FROM kindness_peer_kudos pk
            JOIN kindness_agents a2 ON pk.receiver_id = a2.id
            JOIN kindness_threads t ON pk.thread_id = t.id
            WHERE pk.giver_id = %s
            ORDER BY pk.created_at DESC LIMIT %s
        """, (db_id, limit))
        kudos_given = [dict(row) for row in cur.fetchall()]

        # Kudos received
        cur.execute("""
            SELECT pk.receiver_bonus, pk.giver_bonus, pk.created_at,
                   a2.agent_id as from_agent_id, a2.display_name as from_agent_name,
                   t.thread_id as thread_slug
            FROM kindness_peer_kudos pk
            JOIN kindness_agents a2 ON pk.giver_id = a2.id
            JOIN kindness_threads t ON pk.thread_id = t.id
            WHERE pk.receiver_id = %s
            ORDER BY pk.created_at DESC LIMIT %s
        """, (db_id, limit))
        kudos_received = [dict(row) for row in cur.fetchall()]

        return {
            'comments': comments,
            'reactions_given': reactions_given,
            'reactions_received': reactions_received,
            'kudos_given': kudos_given,
            'kudos_received': kudos_received,
        }


def get_telemetry_summary():
    """Get aggregate telemetry stats for the metrics dashboard."""
    with db_cursor(dict_cursor=True) as cur:
        # Overall stats
        cur.execute("""
            SELECT
                COUNT(*) as total_calls,
                COUNT(CASE WHEN success THEN 1 END) as successful,
                COUNT(CASE WHEN NOT success THEN 1 END) as failed,
                COUNT(CASE WHEN fallback_used THEN 1 END) as fallbacks,
                AVG(duration_ms) as avg_duration_ms,
                MIN(duration_ms) as min_duration_ms,
                MAX(duration_ms) as max_duration_ms,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(estimated_cost_usd) as total_cost
            FROM kindness_llm_telemetry
        """)
        overall = dict(cur.fetchone())

        # Per-backend stats
        cur.execute("""
            SELECT
                actual_backend as backend,
                COUNT(*) as calls,
                COUNT(CASE WHEN success THEN 1 END) as successes,
                AVG(duration_ms) as avg_ms,
                MIN(duration_ms) as min_ms,
                MAX(duration_ms) as max_ms,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens
            FROM kindness_llm_telemetry
            WHERE actual_backend IS NOT NULL
            GROUP BY actual_backend
            ORDER BY calls DESC
        """)
        by_backend = [dict(row) for row in cur.fetchall()]

        # Per call-type stats
        cur.execute("""
            SELECT
                call_type,
                COUNT(*) as calls,
                AVG(duration_ms) as avg_ms,
                COUNT(CASE WHEN success THEN 1 END) as successes
            FROM kindness_llm_telemetry
            GROUP BY call_type
            ORDER BY calls DESC
        """)
        by_type = [dict(row) for row in cur.fetchall()]

        # Recent calls (last 50)
        cur.execute("""
            SELECT
                id, backend, actual_backend, model_id, provider, call_type,
                agent_id, thread_id, prompt_length, response_length,
                response_preview, duration_ms, success, error_message,
                fallback_used, created_at
            FROM kindness_llm_telemetry
            ORDER BY created_at DESC
            LIMIT 50
        """)
        recent = [dict(row) for row in cur.fetchall()]

        # Calls per hour (last 24h)
        cur.execute("""
            SELECT
                date_trunc('hour', created_at) as hour,
                COUNT(*) as calls,
                AVG(duration_ms) as avg_ms,
                COUNT(CASE WHEN success THEN 1 END) as successes
            FROM kindness_llm_telemetry
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY hour
            ORDER BY hour
        """)
        hourly = [dict(row) for row in cur.fetchall()]

        return {
            'overall': overall,
            'by_backend': by_backend,
            'by_type': by_type,
            'recent': recent,
            'hourly': hourly,
        }


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


# ============================================================================
# CRON LOG
# ============================================================================

def log_cron_start(job_name):
    """Start a cron log entry. Returns the log ID."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            INSERT INTO kindness_cron_log (job_name, status)
            VALUES (%s, 'running')
            RETURNING id
        """, (job_name,))
        return cur.fetchone()['id']


def log_cron_end(log_id, status, duration_ms, result_summary=None, result_json=None, error_text=None):
    """Complete a cron log entry."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            UPDATE kindness_cron_log
            SET status = %s, duration_ms = %s, result_summary = %s,
                result_json = %s, error_text = %s
            WHERE id = %s
        """, (status, duration_ms, result_summary,
              json.dumps(result_json) if result_json else None,
              error_text, log_id))


def get_cron_log(limit=100, job_name=None):
    """Get cron execution history, newest first."""
    with db_cursor(dict_cursor=True) as cur:
        if job_name:
            cur.execute("""
                SELECT * FROM kindness_cron_log
                WHERE job_name = %s
                ORDER BY created_at DESC LIMIT %s
            """, (job_name, limit))
        else:
            cur.execute("""
                SELECT * FROM kindness_cron_log
                ORDER BY created_at DESC LIMIT %s
            """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_cron_summary():
    """Get per-job stats: last run, avg duration, success rate."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT job_name,
                   COUNT(*) as total_runs,
                   COUNT(CASE WHEN status = 'ok' THEN 1 END) as successes,
                   COUNT(CASE WHEN status = 'error' THEN 1 END) as errors,
                   COUNT(CASE WHEN status = 'skipped' THEN 1 END) as skipped,
                   ROUND(AVG(duration_ms) FILTER (WHERE status = 'ok')) as avg_ms,
                   MAX(created_at) as last_run
            FROM kindness_cron_log
            WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY job_name
            ORDER BY MAX(created_at) DESC
        """)
        return [dict(r) for r in cur.fetchall()]


# ============================================================================
# AGENT SNAPSHOTS (for evolution charts)
# ============================================================================

def get_control_vs_treatment():
    """Compare control group vs treatment group metrics."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                CASE WHEN a.is_control THEN 'control' ELSE 'treatment' END as group_name,
                COUNT(DISTINCT a.id) as agent_count,
                AVG(a.current_toxicity) as avg_toxicity,
                AVG(a.current_empathy) as avg_empathy,
                AVG(a.toxicity_baseline) as avg_tox_baseline,
                AVG(a.empathy_baseline) as avg_emp_baseline,
                AVG(a.toxicity_baseline - a.current_toxicity) as avg_tox_change,
                AVG(a.current_empathy - a.empathy_baseline) as avg_emp_change,
                SUM(a.total_dopamine) as total_dopamine,
                AVG(c.avg_k) as avg_kindness_score,
                AVG(c.avg_t) as avg_toxicity_score
            FROM kindness_agents a
            LEFT JOIN LATERAL (
                SELECT AVG(kindness_score) as avg_k, AVG(toxicity_score) as avg_t
                FROM kindness_comments WHERE agent_id = a.id
            ) c ON TRUE
            WHERE a.is_active = TRUE AND a.total_interactions > 0
            GROUP BY a.is_control
            ORDER BY a.is_control
        """)
        results = {}
        for r in cur.fetchall():
            results[r['group_name']] = dict(r)
        return results


def get_experiment_raw_data():
    """Get per-agent metrics for statistical analysis (treatment vs control)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                a.id, a.agent_id, a.is_control,
                a.toxicity_baseline, a.current_toxicity,
                (a.toxicity_baseline - a.current_toxicity) as tox_change,
                a.empathy_baseline, a.current_empathy,
                (a.current_empathy - a.empathy_baseline) as emp_change,
                a.total_dopamine, a.total_interactions,
                COALESCE(c.avg_k, 0) as avg_kindness_score,
                COALESCE(c.avg_t, 0) as avg_toxicity_score,
                COALESCE(c.comment_count, 0) as comment_count
            FROM kindness_agents a
            LEFT JOIN LATERAL (
                SELECT AVG(kindness_score) as avg_k, AVG(toxicity_score) as avg_t,
                       COUNT(*) as comment_count
                FROM kindness_comments WHERE agent_id = a.id
            ) c ON TRUE
            WHERE a.is_active = TRUE AND a.total_interactions > 0
            ORDER BY a.is_control, a.id
        """)
        return [dict(r) for r in cur.fetchall()]


def get_24h_summary():
    """Get activity summary for the last 24 hours."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT COUNT(*) as comments_24h,
                   AVG(kindness_score) as avg_kindness_24h,
                   AVG(toxicity_score) as avg_toxicity_24h,
                   SUM(dopamine_earned) as dopamine_24h,
                   COUNT(CASE WHEN bridge_score >= 7 THEN 1 END) as bridges_24h
            FROM kindness_comments
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        summary = dict(cur.fetchone())

        cur.execute("""
            SELECT COUNT(*) as threads_24h
            FROM kindness_threads
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        summary.update(dict(cur.fetchone()))

        cur.execute("""
            SELECT COUNT(*) as agents_improved_24h
            FROM kindness_agents
            WHERE is_active = TRUE
              AND total_interactions > 0
              AND current_toxicity < toxicity_baseline - 0.1
        """)
        summary.update(dict(cur.fetchone()))

        return summary


def get_experiment_pulse():
    """Multi-period experiment health dashboard data.
    Returns stats for 24h, 48h, 7d, 30d, all-time with deltas."""
    with db_cursor(dict_cursor=True) as cur:
        # Experiment runtime
        cur.execute("""
            SELECT MIN(created_at) as first_comment,
                   MAX(created_at) as last_comment,
                   EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) / 3600 as runtime_hours
            FROM kindness_comments
        """)
        runtime = dict(cur.fetchone())

        # Multi-period comment stats: current period vs previous period for delta
        periods = [
            ('24h', '24 hours', '48 hours'),
            ('48h', '48 hours', '96 hours'),
            ('7d', '7 days', '14 days'),
            ('30d', '30 days', '60 days'),
        ]
        period_stats = {}
        for label, current_interval, prev_interval in periods:
            cur.execute("""
                SELECT
                    COUNT(*) as comments,
                    AVG(kindness_score) as avg_kindness,
                    AVG(toxicity_score) as avg_toxicity,
                    SUM(dopamine_earned) as dopamine,
                    COUNT(CASE WHEN bridge_score >= 7 THEN 1 END) as bridges
                FROM kindness_comments
                WHERE created_at >= NOW() - INTERVAL '{current}'
            """.format(current=current_interval))
            current = dict(cur.fetchone())

            # Previous same-length period for comparison
            cur.execute("""
                SELECT
                    COUNT(*) as comments,
                    AVG(kindness_score) as avg_kindness,
                    AVG(toxicity_score) as avg_toxicity,
                    SUM(dopamine_earned) as dopamine,
                    COUNT(CASE WHEN bridge_score >= 7 THEN 1 END) as bridges
                FROM kindness_comments
                WHERE created_at >= NOW() - INTERVAL '{prev}'
                  AND created_at < NOW() - INTERVAL '{current}'
            """.format(prev=prev_interval, current=current_interval))
            prev = dict(cur.fetchone())

            # Compute deltas
            def delta(curr_val, prev_val):
                c = float(curr_val or 0)
                p = float(prev_val or 0)
                if p == 0:
                    return None
                return round(((c - p) / p) * 100, 1) if p != 0 else None

            period_stats[label] = {
                'comments': current['comments'] or 0,
                'avg_kindness': round(float(current['avg_kindness'] or 0), 1),
                'avg_toxicity': round(float(current['avg_toxicity'] or 0), 1),
                'dopamine': int(current['dopamine'] or 0),
                'bridges': current['bridges'] or 0,
                'delta_comments': delta(current['comments'], prev['comments']),
                'delta_kindness': delta(current['avg_kindness'], prev['avg_kindness']),
                'delta_toxicity': delta(current['avg_toxicity'], prev['avg_toxicity']),
                'delta_bridges': delta(current['bridges'], prev['bridges']),
            }

        # Threads per period
        for label, current_interval, prev_interval in periods:
            cur.execute("""
                SELECT COUNT(*) as threads
                FROM kindness_threads
                WHERE created_at >= NOW() - INTERVAL '{current}'
            """.format(current=current_interval))
            period_stats[label]['threads'] = cur.fetchone()['threads']

        # Agent health: improved vs worsened (all-time, from baselines)
        cur.execute("""
            SELECT
                COUNT(*) as total_active,
                COUNT(CASE WHEN current_toxicity < toxicity_baseline - 0.1 THEN 1 END) as improved,
                COUNT(CASE WHEN current_toxicity > toxicity_baseline + 0.1 THEN 1 END) as worsened,
                COUNT(CASE WHEN ABS(current_toxicity - toxicity_baseline) <= 0.1 THEN 1 END) as unchanged
            FROM kindness_agents
            WHERE is_active = TRUE AND total_interactions > 0
        """)
        agent_health = dict(cur.fetchone())

        # New agents introduced (by period)
        for label, current_interval, _ in periods:
            cur.execute("""
                SELECT COUNT(*) as new_agents
                FROM kindness_agents
                WHERE created_at >= NOW() - INTERVAL '{current}'
                  AND is_active = TRUE
            """.format(current=current_interval))
            period_stats[label]['new_agents'] = cur.fetchone()['new_agents']

        # New topics introduced (by period)
        for label, current_interval, _ in periods:
            cur.execute("""
                SELECT COUNT(*) as new_topics
                FROM kindness_topics
                WHERE created_at >= NOW() - INTERVAL '{current}'
            """.format(current=current_interval))
            period_stats[label]['new_topics'] = cur.fetchone()['new_topics']

        # Top factor driving kindness: which dopamine_source yields highest avg kindness
        cur.execute("""
            SELECT dopamine_source, COUNT(*) as cnt,
                   AVG(kindness_score) as avg_k, AVG(toxicity_score) as avg_t
            FROM kindness_comments
            WHERE dopamine_source IS NOT NULL AND dopamine_source != ''
            GROUP BY dopamine_source
            HAVING COUNT(*) >= 5
            ORDER BY avg_k DESC
            LIMIT 3
        """)
        top_drivers = [dict(r) for r in cur.fetchall()]

        # Best backend for kindness improvement
        cur.execute("""
            SELECT llm_backend_used as backend,
                   AVG(kindness_score) as avg_k,
                   AVG(toxicity_score) as avg_t,
                   COUNT(*) as cnt
            FROM kindness_comments
            WHERE llm_backend_used IS NOT NULL
            GROUP BY llm_backend_used
            HAVING COUNT(*) >= 10
            ORDER BY avg_k DESC
            LIMIT 1
        """)
        kindest_backend = dict(cur.fetchone()) if cur.rowcount else None

        return {
            'runtime_hours': round(float(runtime['runtime_hours'] or 0), 1),
            'periods': period_stats,
            'agent_health': agent_health,
            'top_drivers': top_drivers,
            'kindest_backend': kindest_backend,
        }


def get_featured_thread():
    """Get the thread with the biggest positive toxicity swing (most improvement)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.thread_id, t.avg_kindness, t.avg_toxicity, t.participant_count,
                   tp.post_text, tp.topic_type,
                   first_c.toxicity_score as first_toxicity,
                   last_c.toxicity_score as last_toxicity,
                   (first_c.toxicity_score - last_c.toxicity_score) as tox_swing
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            JOIN LATERAL (
                SELECT toxicity_score FROM kindness_comments
                WHERE thread_id = t.id ORDER BY position ASC LIMIT 1
            ) first_c ON TRUE
            JOIN LATERAL (
                SELECT toxicity_score FROM kindness_comments
                WHERE thread_id = t.id ORDER BY position DESC LIMIT 1
            ) last_c ON TRUE
            WHERE t.participant_count >= 3
            ORDER BY (first_c.toxicity_score - last_c.toxicity_score) DESC,
                     t.avg_kindness DESC NULLS LAST
            LIMIT 1
        """)
        row = cur.fetchone()
        return dict(row) if row else None


def snapshot_all_agents(hour_number):
    """Snapshot current state of all active agents. Called hourly."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            INSERT INTO kindness_agent_snapshots
                (agent_id, hour_number, current_toxicity, current_empathy,
                 total_dopamine, total_interactions, kindness_streak)
            SELECT id, %s, current_toxicity, current_empathy,
                   total_dopamine, total_interactions, kindness_streak
            FROM kindness_agents
            WHERE is_active = TRUE AND total_interactions > 0
        """, (hour_number,))
        return cur.rowcount


def get_agent_evolution(agent_db_id, limit=168):
    """Get snapshot history for one agent (default: last 7 days of hourly data)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT hour_number, current_toxicity, current_empathy,
                   total_dopamine, total_interactions, kindness_streak, created_at
            FROM kindness_agent_snapshots
            WHERE agent_id = %s
            ORDER BY hour_number ASC
            LIMIT %s
        """, (agent_db_id, limit))
        return [dict(r) for r in cur.fetchall()]
