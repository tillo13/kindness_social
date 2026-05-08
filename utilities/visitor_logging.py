"""Shared visitor-logging module for ALL of Andy's Flask apps.

Lives at: ~/Desktop/code/_infrastructure/visitor_logging/visitor_logging.py
Synced into each project's utilities/ via deploy.json shared_files.

Purpose:
- Log every public page view across all 12+ Andy sites into one shared table
  (kumori_ops.visitor_log) so Andy can see crawler traffic, real human clicks,
  and reverse-sell DM-link engagement portfolio-wide.
- Self-contained: brings its own psycopg2 + Secret Manager creds, depends on
  nothing in the consuming app. Mirrors anthropic_logger.py's shape.

Install per-app — two lines in app.py:

    from utilities.visitor_logging import install_middleware
    install_middleware(app, 'inroads')   # pass your site's slug

That's it. Every GET request automatically logged. Failures are swallowed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_KUMORI_PROJECT = 'kumori-404602'

# ─── DB creds (cached) ────────────────────────────────────────────────────────

_DB_CREDS_CACHE: Optional[dict] = None


def _get_db_creds() -> dict:
    global _DB_CREDS_CACHE
    if _DB_CREDS_CACHE:
        return _DB_CREDS_CACHE
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()

    def fetch(name: str) -> str:
        path = f"projects/{_KUMORI_PROJECT}/secrets/{name}/versions/latest"
        return client.access_secret_version(request={"name": path}).payload.data.decode("UTF-8")

    _DB_CREDS_CACHE = {
        'host': fetch('KUMORI_POSTGRES_IP'),
        'dbname': fetch('KUMORI_POSTGRES_DB_NAME'),
        'user': fetch('KUMORI_POSTGRES_USERNAME'),
        'password': fetch('KUMORI_POSTGRES_PASSWORD'),
        'connection_name': fetch('KUMORI_POSTGRES_CONNECTION_NAME'),
    }
    return _DB_CREDS_CACHE


def _connect():
    import psycopg2
    creds = _get_db_creds()
    is_gcp = os.environ.get('GAE_ENV', '').startswith('standard') or os.path.exists('/cloudsql')
    if is_gcp:
        socket_dir = os.environ.get('DB_SOCKET_DIR', '/cloudsql')
        host = f"{socket_dir}/{creds['connection_name']}"
    else:
        host = creds['host']
    return psycopg2.connect(
        host=host, dbname=creds['dbname'], user=creds['user'],
        password=creds['password'], connect_timeout=5,
        options='-c statement_timeout=5000',
    )


# ─── Bot detection / source classification ───────────────────────────────────

_BOT_UA_MARKERS = (
    'bot', 'crawler', 'spider', 'preview', 'unfurl', 'fetch',
    'linkedinbot', 'slackbot', 'twitterbot', 'facebookexternalhit',
    'whatsapp', 'discordbot', 'telegrambot', 'googlebot', 'bingbot',
    'duckduckbot', 'applebot', 'embedly', 'curl', 'wget', 'python-requests',
)


def _looks_like_bot(user_agent: Optional[str]) -> bool:
    if not user_agent:
        return True
    return any(m in user_agent.lower() for m in _BOT_UA_MARKERS)


_SOURCE_RULES = (
    ('linkedinbot', 'LinkedIn'),
    ('slackbot', 'Slack'),
    ('twitterbot', 'Twitter'),
    ('facebookexternalhit', 'Facebook'),
    ('whatsapp', 'WhatsApp'),
    ('discordbot', 'Discord'),
    ('telegrambot', 'Telegram'),
    ('gptbot', 'GPTBot (OpenAI)'),
    ('oai-searchbot', 'OAI-SearchBot'),
    ('chatgpt-user', 'ChatGPT-User'),
    ('claudebot', 'ClaudeBot (Anthropic)'),
    ('claude-searchbot', 'Claude-SearchBot'),
    ('claude-user', 'Claude-User'),
    ('perplexitybot', 'Perplexity'),
    ('perplexity-user', 'Perplexity-User'),
    ('ccbot', 'CommonCrawl'),
    ('bytespider', 'Bytespider (TikTok)'),
    ('google-extended', 'Google-Extended'),
    ('googlebot', 'Googlebot'),
    ('bingbot', 'Bingbot'),
    ('duckduckbot', 'DuckDuckBot'),
    ('applebot', 'Applebot'),
    ('ahrefsbot', 'Ahrefs'),
    ('semrushbot', 'Semrush'),
    ('mj12bot', 'Majestic'),
    ('embedly', 'Embedly'),
    ('curl/', 'curl'),
    ('wget/', 'wget'),
    ('python-requests', 'python-requests'),
)


def classify_user_agent(user_agent: Optional[str]) -> str:
    """Short human-readable source label for a UA string."""
    if not user_agent:
        return 'Unknown'
    ua = user_agent.lower()
    for marker, label in _SOURCE_RULES:
        if marker in ua:
            return label
    if 'mozilla' in ua and 'bot' not in ua and 'crawler' not in ua:
        return 'Browser'
    return 'Other'


# ─── Insert (fire-and-forget, daemon thread) ──────────────────────────────────

def _insert_view(app_name: str, path: str, ip: str, user_agent: str,
                 referrer: str, is_bot: bool, is_authed: bool):
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO kumori_ops.visitor_log
                    (app, path, ip, user_agent, referrer, is_bot, is_authed)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                (app_name or '')[:64], (path or '')[:1000], (ip or '')[:64],
                (user_agent or '')[:1000], (referrer or '')[:1000],
                bool(is_bot), bool(is_authed),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"visitor_logging: insert failed: {e}")


def log_view(app_name: str, path: str, ip: str = '', user_agent: str = '',
             referrer: str = '', is_authed: bool = False) -> None:
    """Async-fire one row. Caller must NOT await — returns immediately."""
    is_bot = _looks_like_bot(user_agent)
    t = threading.Thread(
        target=_insert_view,
        args=(app_name, path, ip, user_agent, referrer, is_bot, is_authed),
        daemon=True,
    )
    t.start()


# ─── Flask middleware install ────────────────────────────────────────────────

# Paths whose hits we don't care about (cuts noise + avoids logging the
# dashboard itself, which would create a feedback loop).
_SKIP_PATH_PREFIXES = (
    '/static/', '/admin/', '/api/admin/', '/health', '/healthz',
    '/favicon.ico', '/robots.txt', '/sitemap.xml',
)


def install_middleware(flask_app, app_name: str, *,
                       authed_check=None,
                       skip_prefixes: tuple = _SKIP_PATH_PREFIXES):
    """Register a before_request hook that logs every public GET to
    kumori_ops.visitor_log under `app_name`. Idempotent — safe to call twice.

    `authed_check` is an optional callable taking no args returning bool;
    defaults to checking Flask `session` for a 'user' key.

    `skip_prefixes` overrides the default list of paths to skip."""
    from flask import request, session

    if getattr(flask_app, '_visitor_logging_installed', False):
        return
    flask_app._visitor_logging_installed = True

    def _default_authed():
        try:
            return 'user' in session
        except Exception:
            return False

    auth_fn = authed_check or _default_authed

    @flask_app.before_request
    def _visitor_log_hook():
        try:
            if request.method != 'GET':
                return None
            path = request.path or ''
            for p in skip_prefixes:
                if path.startswith(p):
                    return None
            ip = (request.headers.get('X-Forwarded-For') or
                  request.remote_addr or '').split(',')[0].strip()
            log_view(
                app_name=app_name,
                path=path,
                ip=ip,
                user_agent=request.headers.get('User-Agent', ''),
                referrer=request.headers.get('Referer', ''),
                is_authed=bool(auth_fn()),
            )
        except Exception as e:
            logger.warning(f"visitor_logging hook error: {e}")
        return None


# ─── Read helpers (used by dashboards) ───────────────────────────────────────

def get_recent_views(app_name: Optional[str] = None, limit: int = 300,
                     human_only: bool = True):
    """Return recent rows. Each row is a dict with `source` + `from_linkedin`
    fields derived in Python (so dashboards don't need to re-derive)."""
    sql = """
        SELECT id, app, path, ip, user_agent, referrer,
               is_bot, is_authed, viewed_at
        FROM kumori_ops.visitor_log
        WHERE (%s = FALSE OR is_bot = FALSE)
          AND (%s::text IS NULL OR app = %s)
        ORDER BY viewed_at DESC
        LIMIT %s
    """
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, (human_only, app_name, app_name, limit))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"visitor_logging.get_recent_views: {e}")
        return []
    for r in rows:
        r['source'] = classify_user_agent(r.get('user_agent'))
        ref = (r.get('referrer') or '').lower()
        r['from_linkedin'] = 'linkedin.com' in ref or 'lnkd.in' in ref
    return rows


def get_stats(app_name: Optional[str] = None, hours: int = 24) -> dict:
    """Counts of humans / LinkedIn-referred / bots in the last N hours, plus
    a top-source breakdown for the bot side, and per-app totals if no filter."""
    out = {
        'humans': 0, 'from_linkedin': 0, 'bots': 0, 'total': 0,
        'bot_breakdown': [], 'app_breakdown': [],
        'window_hours': hours, 'app_filter': app_name,
    }
    try:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE is_bot = FALSE) AS humans,
                    COUNT(*) FILTER (WHERE is_bot = FALSE
                                      AND (LOWER(referrer) LIKE %s
                                           OR LOWER(referrer) LIKE %s)) AS from_linkedin,
                    COUNT(*) FILTER (WHERE is_bot = TRUE) AS bots,
                    COUNT(*) AS total
                FROM kumori_ops.visitor_log
                WHERE viewed_at > NOW() - (%s || ' hours')::interval
                  AND (%s::text IS NULL OR app = %s)
            """, ('%linkedin.com%', '%lnkd.in%', hours, app_name, app_name))
            row = cur.fetchone()
            out['humans'], out['from_linkedin'], out['bots'], out['total'] = row

            cur.execute("""
                SELECT user_agent, COUNT(*) AS n
                FROM kumori_ops.visitor_log
                WHERE is_bot = TRUE
                  AND viewed_at > NOW() - (%s || ' hours')::interval
                  AND (%s::text IS NULL OR app = %s)
                GROUP BY user_agent
                ORDER BY n DESC
                LIMIT 8
            """, (hours, app_name, app_name))
            out['bot_breakdown'] = [
                {'source': classify_user_agent(ua), 'count': n}
                for ua, n in cur.fetchall()
            ]

            if app_name is None:
                cur.execute("""
                    SELECT app,
                           COUNT(*) FILTER (WHERE is_bot = FALSE) AS humans,
                           COUNT(*) FILTER (WHERE is_bot = TRUE)  AS bots,
                           COUNT(*) AS total
                    FROM kumori_ops.visitor_log
                    WHERE viewed_at > NOW() - (%s || ' hours')::interval
                    GROUP BY app
                    ORDER BY total DESC
                """, (hours,))
                out['app_breakdown'] = [
                    {'app': a, 'humans': h, 'bots': b, 'total': t}
                    for a, h, b, t in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"visitor_logging.get_stats: {e}")
    return out
