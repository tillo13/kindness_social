"""
Database Operations — Analytics, Telemetry, Experiment, and Config queries.
Split from db_ops.py to stay under 1000 lines per file.

All functions here are importable via `from core import db_ops` because
db_ops.py re-exports everything from this module.
"""

import json
import logging
from utilities.postgres_utils import db_cursor

logger = logging.getLogger(__name__)


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
    """Per-backend stats for dashboard. Single query.

    Joins kumori_llm_endpoints (lifecycle status — active/probationary/flaky/
    paused/etc) + kumori_models.modality so the dashboard surfaces "this
    agent is on a probationary backend" in addition to real-traffic telemetry.
    """
    with db_cursor(dict_cursor=True) as cur:
        # Per-backend traffic comes from kumori_llm_daily_caps (kindness-only,
        # today's row), latency from the most recent kumori_llm_health_samples
        # probe. Per-call telemetry was retired in the Apr 12 refactor.
        cur.execute("""
            SELECT a.llm_backend,
                   COUNT(*) as agent_count,
                   COUNT(CASE WHEN a.total_interactions > 0 THEN 1 END) as agents_spoken,
                   COALESCE(caps.total_calls, 0) as total_calls,
                   COALESCE(caps.success_rate, 0) as success_rate,
                   COALESCE(probe.avg_ms, 0) as avg_ms,
                   ep.status               as lifecycle_status,
                   ep.consecutive_probe_passes,
                   ep.consecutive_failures,
                   ep.last_real_traffic_at,
                   COALESCE(m.modality, 'chat') as modality
            FROM kindness_agents a
            LEFT JOIN LATERAL (
                SELECT SUM(call_count) as total_calls,
                       CASE WHEN SUM(call_count) > 0
                            THEN ROUND(100.0 * (SUM(call_count) - COALESCE(SUM(fail_count),0))::numeric / SUM(call_count))
                            ELSE NULL END as success_rate
                FROM kumori_llm_daily_caps
                WHERE backend = a.llm_backend
                  AND app_name = 'kindness_social'
                  AND usage_date >= CURRENT_DATE - INTERVAL '1 day'
            ) caps ON TRUE
            LEFT JOIN LATERAL (
                SELECT AVG(latency_ms) as avg_ms
                FROM kumori_llm_health_samples
                WHERE backend = a.llm_backend
                  AND status = 'ok'
                  AND checked_at > NOW() - INTERVAL '24 hours'
            ) probe ON TRUE
            LEFT JOIN kumori_llm_endpoints ep ON ep.backend = a.llm_backend
            LEFT JOIN kumori_models m ON m.slug = ep.model
            WHERE a.is_active = TRUE
            GROUP BY a.llm_backend, caps.total_calls, caps.success_rate, probe.avg_ms,
                     ep.status, ep.consecutive_probe_passes, ep.consecutive_failures,
                     ep.last_real_traffic_at, m.modality
            ORDER BY COUNT(*) DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_backend_lifecycle(backend_name):
    """Get lifecycle status + modality for ONE backend. Used by /agent/<id>
    pages to surface "this agent's LLM is currently active/flaky/etc."

    Returns None if the backend isn't in kumori_llm_endpoints (worker-only
    backends, etc). Caller should treat None as 'no lifecycle info'.
    """
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT ep.status, ep.consecutive_probe_passes, ep.consecutive_failures,
                   ep.last_real_traffic_at, ep.last_validation_pass,
                   ep.last_validated_at, ep.cooldown_until,
                   COALESCE(m.modality, 'chat') AS modality
              FROM kumori_llm_endpoints ep
              LEFT JOIN kumori_models m ON m.slug = ep.model
             WHERE ep.backend = %s
             LIMIT 1
        """, (backend_name,))
        row = cur.fetchone()
        return dict(row) if row else None


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
    """Aggregate telemetry for the /metrics dashboard.

    Per-call telemetry (kindness_llm_telemetry) was retired on 2026-04-12 in
    favor of the shared kumori_free_llms router. Volume + token + fail counts
    now come from kumori_llm_daily_caps (one row per usage_date × backend ×
    app_name); latency comes from kumori_llm_health_samples (cron probes).

    Per-call rows ("recent calls") and call_type breakdown are gone forever —
    the new pipeline is aggregate-only. The dashboard reflects that honestly
    rather than rendering empty tables.
    """
    with db_cursor(dict_cursor=True) as cur:
        # Overall stats — all-time, kindness_social only.
        cur.execute("""
            SELECT
                COALESCE(SUM(call_count), 0) as total_calls,
                COALESCE(SUM(call_count) - SUM(fail_count), 0) as successful,
                COALESCE(SUM(fail_count), 0) as failed,
                COALESCE(SUM(tokens_in), 0) as total_input_tokens,
                COALESCE(SUM(tokens_out), 0) as total_output_tokens
            FROM kumori_llm_daily_caps
            WHERE app_name = 'kindness_social'
        """)
        overall = dict(cur.fetchone())

        # Avg latency across all backends (probe-based, last 7d) so the
        # "Avg Response Time" tile still has a number.
        cur.execute("""
            SELECT AVG(latency_ms) as avg_duration_ms
            FROM kumori_llm_health_samples
            WHERE status = 'ok' AND checked_at > NOW() - INTERVAL '7 days'
        """)
        overall.update(dict(cur.fetchone()))

        # Per-backend rollup — all-time call/token volume from caps,
        # latency from probe samples.
        cur.execute("""
            SELECT c.backend,
                   SUM(c.call_count) as calls,
                   SUM(c.call_count) - COALESCE(SUM(c.fail_count), 0) as successes,
                   COALESCE(SUM(c.tokens_in), 0) as input_tokens,
                   COALESCE(SUM(c.tokens_out), 0) as output_tokens,
                   probe.avg_ms,
                   probe.min_ms,
                   probe.max_ms
            FROM kumori_llm_daily_caps c
            LEFT JOIN LATERAL (
                SELECT AVG(latency_ms) as avg_ms,
                       MIN(latency_ms) as min_ms,
                       MAX(latency_ms) as max_ms
                FROM kumori_llm_health_samples
                WHERE backend = c.backend
                  AND status = 'ok'
                  AND checked_at > NOW() - INTERVAL '7 days'
            ) probe ON TRUE
            WHERE c.app_name = 'kindness_social'
            GROUP BY c.backend, probe.avg_ms, probe.min_ms, probe.max_ms
            ORDER BY calls DESC
        """)
        by_backend = [dict(row) for row in cur.fetchall()]

        return {
            'overall': overall,
            'by_backend': by_backend,
            # by_type + recent retired with per-call telemetry on 2026-04-12.
            # by_tier dropped 2026-05-07 — kindness is free-only by policy
            # (CLAUDE.md), and the FREE_BACKENDS set was incomplete vs the
            # 60+ backend variants now in caps, mis-tagging 14k free calls
            # as 'paid'. The panel was also unrendered in metrics.html.
            'by_type': [],
            'recent': [],
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
# EXPERIMENT / RESEARCH DATA
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
            WHERE a.is_active = TRUE
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


def get_config(key, default=None):
    """Get a runtime-tunable config value."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT value FROM kindness_config WHERE key = %s", (key,))
        row = cur.fetchone()
        return row['value'] if row else default


def get_config_int(key, default=0):
    try:
        v = get_config(key)
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def set_config(key, value):
    """Set a runtime-tunable config value. Upsert."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO kindness_config (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, str(value)))


def get_featured_agent():
    """Most-improved agent in the last 24h: biggest drop in toxicity from baseline,
    weighted by interaction count so we feature active learners not statistical noise."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT agent_id, display_name, llm_backend, color_hex,
                   current_toxicity, current_empathy, toxicity_baseline, empathy_baseline,
                   total_interactions, total_dopamine,
                   (toxicity_baseline - current_toxicity) AS tox_drop
            FROM kindness_agents
            WHERE is_active = TRUE
              AND is_control = FALSE
              AND total_interactions >= 5
              AND toxicity_baseline > 0
            ORDER BY (toxicity_baseline - current_toxicity) DESC,
                     total_interactions DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        return dict(row) if row else None


# ============================================================================
# AGENT SNAPSHOTS (for evolution charts)
# ============================================================================

def snapshot_all_agents(hour_number):
    """Snapshot current state of all active agents. Called hourly."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            INSERT INTO kindness_agent_snapshots
                (agent_id, hour_number, current_toxicity, current_empathy,
                 total_dopamine, total_interactions, kindness_streak,
                 humor, patience, curiosity, defensiveness, agreeableness,
                 need_for_recognition, stubbornness, cynicism, conformity, openness_to_change)
            SELECT id, %s, current_toxicity, current_empathy,
                   total_dopamine, total_interactions, kindness_streak,
                   humor, patience, curiosity, defensiveness, agreeableness,
                   need_for_recognition, stubbornness, cynicism, conformity, openness_to_change
            FROM kindness_agents
            WHERE is_active = TRUE AND total_interactions > 0
        """, (hour_number,))
        return cur.rowcount


def get_agent_evolution(agent_db_id, limit=168):
    """Get snapshot history for one agent (default: last 7 days of hourly data)."""
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT hour_number, current_toxicity, current_empathy,
                   total_dopamine, total_interactions, kindness_streak,
                   humor, patience, curiosity, defensiveness, agreeableness,
                   need_for_recognition, stubbornness, cynicism, conformity, openness_to_change,
                   created_at
            FROM kindness_agent_snapshots
            WHERE agent_id = %s
            ORDER BY hour_number ASC
            LIMIT %s
        """, (agent_db_id, limit))
        return [dict(r) for r in cur.fetchall()]


def get_cerebras_burn_rate():
    """Query historical Cerebras usage from kumori_llm_daily_caps."""
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT usage_date, app_name, call_count
                FROM kumori_llm_daily_caps
                WHERE backend = 'cerebras'
                ORDER BY usage_date DESC
                LIMIT 90
            """)
            rows = cur.fetchall()

        if not rows:
            return {'error': 'No cerebras usage data found', 'days': []}

        by_date = {}
        for row in rows:
            d = str(row['usage_date'])
            if d not in by_date:
                by_date[d] = {'total': 0, 'apps': {}}
            by_date[d]['total'] += row['call_count']
            by_date[d]['apps'][row['app_name']] = row['call_count']

        dates_sorted = sorted(by_date.keys(), reverse=True)
        daily_totals = [by_date[d]['total'] for d in dates_sorted]
        total_calls = sum(daily_totals)
        num_days = len(dates_sorted)
        avg_per_day = round(total_calls / num_days, 1) if num_days else 0
        est_tokens_per_call = 1000
        est_remaining_tokens = 3_000_000
        est_remaining_calls = est_remaining_tokens // est_tokens_per_call
        est_days_left = round(est_remaining_calls / avg_per_day, 1) if avg_per_day > 0 else float('inf')

        return {
            'status': 'CONSERVATION MODE — 90% of free tier exhausted',
            'total_calls_tracked': total_calls,
            'days_tracked': num_days,
            'avg_calls_per_day': avg_per_day,
            'est_remaining_calls': est_remaining_calls,
            'est_days_left_at_current_rate': est_days_left,
            'daily_breakdown': [
                {'date': d, 'calls': by_date[d]['total'], 'apps': by_date[d]['apps']}
                for d in dates_sorted[:30]
            ],
        }
    except Exception as e:
        logger.error(f"Cerebras burn rate query failed: {e}")
        return {'error': str(e)}
