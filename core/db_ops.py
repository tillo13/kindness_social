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

            -- Snapshot all 10 personality traits over time (was just toxicity/empathy)
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS humor FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS patience FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS curiosity FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS defensiveness FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS agreeableness FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS need_for_recognition FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS stubbornness FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS cynicism FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS conformity FLOAT;
            ALTER TABLE kindness_agent_snapshots ADD COLUMN IF NOT EXISTS openness_to_change FLOAT;

            -- Tunable runtime config (e.g. revisit intensity dial). Single-row-per-key.
            CREATE TABLE IF NOT EXISTS kindness_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Clean up existing parse-error reflection rows: extract internal_thought
            -- string from raw JSON-ish text and strip the curly braces / json keys.
            UPDATE kindness_reflections
            SET reflection_text = TRIM(BOTH ' "{}[],' FROM
                REGEXP_REPLACE(
                    REGEXP_REPLACE(reflection_text, '^.*"internal_thought"\\s*:\\s*"', ''),
                    '",\\s*"adjustments".*$', ''
                )
            )
            WHERE change_reason = 'parse error — raw thought saved'
              AND reflection_text LIKE '%"internal_thought"%';

            -- One-time backfill: historical snapshots have NULL for the 10 new
            -- personality columns (they were just added). Populate them from
            -- the agent's CURRENT trait values so charts show flat lines until
            -- live hourly snapshots accumulate real movement.
            UPDATE kindness_agent_snapshots s
            SET humor                = COALESCE(s.humor,                a.humor),
                patience             = COALESCE(s.patience,             a.patience),
                curiosity            = COALESCE(s.curiosity,            a.curiosity),
                defensiveness        = COALESCE(s.defensiveness,        a.defensiveness),
                agreeableness        = COALESCE(s.agreeableness,        a.agreeableness),
                need_for_recognition = COALESCE(s.need_for_recognition, a.need_for_recognition),
                stubbornness         = COALESCE(s.stubbornness,         a.stubbornness),
                cynicism             = COALESCE(s.cynicism,             a.cynicism),
                conformity           = COALESCE(s.conformity,           a.conformity),
                openness_to_change   = COALESCE(s.openness_to_change,   a.openness_to_change)
            FROM kindness_agents a
            WHERE s.agent_id = a.id
              AND (s.humor IS NULL OR s.patience IS NULL OR s.curiosity IS NULL);
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


def get_agent_family(agent_db_id):
    """Get an agent's family: parent, siblings, children, grandchildren."""
    with db_cursor(dict_cursor=True) as cur:
        # Parent
        cur.execute("""
            SELECT p.agent_id, p.display_name, p.current_toxicity, p.current_empathy,
                   p.total_dopamine, p.llm_backend, p.color_hex
            FROM kindness_agents a
            JOIN kindness_agents p ON a.invited_by = p.id
            WHERE a.id = %s
        """, (agent_db_id,))
        parent = dict(cur.fetchone()) if cur.rowcount else None

        # Siblings (same parent, excluding self)
        siblings = []
        if parent:
            cur.execute("""
                SELECT a.agent_id, a.display_name, a.current_toxicity, a.current_empathy,
                       a.total_dopamine, a.llm_backend, a.color_hex
                FROM kindness_agents a
                JOIN kindness_agents p ON a.invited_by = p.id
                WHERE p.agent_id = %s AND a.id != %s AND a.is_active = TRUE
                ORDER BY a.created_at
            """, (parent['agent_id'], agent_db_id))
            siblings = [dict(r) for r in cur.fetchall()]

        # Children (invited by this agent)
        cur.execute("""
            SELECT a.agent_id, a.display_name, a.current_toxicity, a.current_empathy,
                   a.total_dopamine, a.llm_backend, a.color_hex, a.created_at
            FROM kindness_agents a
            WHERE a.invited_by = %s AND a.is_active = TRUE
            ORDER BY a.created_at
        """, (agent_db_id,))
        children = [dict(r) for r in cur.fetchall()]

        # Grandchildren
        grandchildren = []
        if children:
            child_ids = [c['agent_id'] for c in children]
            cur.execute("""
                SELECT gc.agent_id, gc.display_name, gc.current_toxicity, gc.current_empathy,
                       gc.total_dopamine, gc.llm_backend, gc.color_hex,
                       p.agent_id as parent_agent_id
                FROM kindness_agents gc
                JOIN kindness_agents p ON gc.invited_by = p.id
                WHERE p.agent_id = ANY(%s) AND gc.is_active = TRUE
                ORDER BY gc.created_at
            """, (child_ids,))
            grandchildren = [dict(r) for r in cur.fetchall()]

        return {
            'parent': parent,
            'siblings': siblings,
            'children': children,
            'grandchildren': grandchildren,
        }


def get_top_recruiters(limit=15):
    """Get agents who invited the most others, with lineage stats."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT p.agent_id, p.display_name, p.current_toxicity, p.current_empathy,
                   p.total_dopamine, p.llm_backend, p.color_hex,
                   COUNT(c.id) as children_count,
                   AVG(c.current_toxicity) as avg_child_toxicity,
                   AVG(c.current_empathy) as avg_child_empathy,
                   AVG(c.total_dopamine) as avg_child_dopamine
            FROM kindness_agents p
            JOIN kindness_agents c ON c.invited_by = p.id
            WHERE c.is_active = TRUE
            GROUP BY p.id
            ORDER BY children_count DESC
            LIMIT %s
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_all_family_trees():
    """Get the full lineage graph for the families page."""
    with db_cursor(dict_cursor=True) as cur:
        # Get all agents with their parent info
        cur.execute("""
            SELECT a.id, a.agent_id, a.display_name, a.current_toxicity, a.current_empathy,
                   a.total_dopamine, a.llm_backend, a.color_hex, a.invited_by,
                   p.agent_id as parent_agent_id, p.display_name as parent_name
            FROM kindness_agents a
            LEFT JOIN kindness_agents p ON a.invited_by = p.id
            WHERE a.is_active = TRUE AND a.invited_by IS NOT NULL
            ORDER BY a.created_at
        """)
        return [dict(r) for r in cur.fetchall()]


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
    """Get the freshest unused topic, skipping any with an open thread already."""
    with db_cursor(dict_cursor=True) as cur:
        # Pick the least-used, newest approved topic that doesn't already have an open thread
        cur.execute("""
            SELECT t.* FROM kindness_topics t
            WHERE t.is_approved = TRUE
              AND NOT EXISTS (
                  SELECT 1 FROM kindness_threads th
                  WHERE th.topic_id = t.id AND th.is_complete = FALSE
              )
            ORDER BY t.times_used ASC, t.created_at DESC
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
    """Get recent threads with topic info and comment counts."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT t.*, tp.post_text, tp.topic_type, tp.controversy_level, tp.submitted_by,
                   cc.cnt as comment_count
            FROM kindness_threads t
            JOIN kindness_topics tp ON t.topic_id = tp.id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as cnt FROM kindness_comments WHERE thread_id = t.id
            ) cc ON TRUE
            WHERE cc.cnt > 0
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
                 gen_time_ms=0, eval_time_ms=0,
                 parent_comment_id=None, replied_to_agent_id=None):
    """Save a comment with its evaluation scores and optional threading."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_comments
                (thread_id, agent_id, position, comment_text,
                 kindness_score, toxicity_score, empathy_score, bridge_score,
                 dopamine_earned, dopamine_source, reward_multiplier,
                 llm_backend_used, generation_time_ms, eval_time_ms,
                 parent_comment_id, replied_to_agent_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            thread_db_id, agent_db_id, position, comment_text,
            scores.get('kindness', 5), scores.get('toxicity', 5),
            scores.get('empathy', 5), scores.get('bridge', 0),
            dopamine, source, multiplier, backend_used,
            gen_time_ms, eval_time_ms,
            parent_comment_id, replied_to_agent_id,
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


def get_leaderboard(sort_by='kindness', limit=10000):
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
            WHERE a.is_active = TRUE
            ORDER BY {order}
            LIMIT %s
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]



# Re-export analytics functions so 'from core import db_ops' keeps working
from core.db_ops_analytics import *  # noqa: F401,F403
