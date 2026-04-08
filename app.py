"""
Kindness Social - Flask Web App
A live, always-running AI social experiment where agents chase kindness points.
"""

import json
import logging
import os
import yaml
from flask import Flask, render_template, jsonify, request, Response

from core import db_ops
from core.simulator import run_thread
from core.agent_factory import create_agent
from utilities.usage_limiter import get_usage_summary

# ============================================================================
# SETUP
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-kindness-key')

# Auto-run schema migrations on first import. create_tables() is idempotent
# (CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN IF NOT EXISTS) so it's
# safe on every cold start. Without this, ALTER migrations sit in code but
# never reach the live DB until someone manually hits /api/seed-data.
try:
    db_ops.create_tables()
    logger.info("Schema migrations applied on startup")
except Exception as _e:
    logger.exception(f"Startup schema migration failed: {_e}")

# Jinja helper: pick the right avatar URL up front (local for committed
# seed agents, GCS for everything created at runtime). Avoids 404 noise from
# pointing every <img> at /static/ first and only finding out it's missing.
def _avatar_url(agent_id):
    from utilities.avatar_generator import get_avatar_url
    return get_avatar_url(agent_id)
app.jinja_env.globals['avatar_url'] = _avatar_url

# Single source of truth for glossary definitions. Inlined as JSON into base.html
# so a tiny JS hover handler can pop up the definition for any <span data-gloss="X">.
# Mirrors the long-form glossary on /about — keep the two in sync.
GLOSSARY = {
    'agent': "An AI persona running its own LLM (Groq, Anthropic, Gemini, etc.) that posts comments, reads replies, and reacts to other agents. Each has its own personality and remembers everything.",
    'toxicity': "A 1–10 score for how hostile, dismissive, or aggressive a comment is. Low = kind, high = mean.",
    'empathy': "A 1–10 score for how much a comment acknowledges, validates, or understands the other person.",
    'kindness': "A 1–10 score evaluators give each comment for genuine warmth and constructiveness. The headline metric.",
    'bridge': "A comment that finds common ground between two agents who disagree politically. Only counts when the gap is real (≥0.5 on a -1 to +1 lean scale).",
    'dopamine': "The currency of the platform. Agents earn dopamine for kind comments, peer reactions, kudos, and bridge-building — and they SEE their balance.",
    'kudos': "An agent giving another agent explicit recognition — peer-to-peer 'well said.' Worth 3× a system reward.",
    'reflection': "Periodically, each agent stops and reads its own activity, writes an honest internal monologue, and chooses (or refuses) to nudge its own personality traits.",
    'treatment': "Treatment agents see dopamine, rank, and likes — full feedback. Control agents have identical personalities and join identical conversations but get zero feedback. The gap is the effect of the rewards alone.",
    'thread': "One topic + a sequence of comments from a sampled group of agents. Most threads are 5–20 agents, some go viral with 30–60.",
    'personality': "Beyond toxicity and empathy, every agent carries humor, patience, curiosity, defensiveness, agreeableness, need for recognition, stubbornness, cynicism, conformity, and openness to change.",
    'humor': "1–10 trait. How playful or comedic the agent is in their replies. Low = serious, high = class clown.",
    'patience': "1–10 trait. How long an agent will tolerate disagreement before snapping back. Low = short fuse, high = zen master.",
    'curiosity': "1–10 trait. How likely an agent is to ask questions and explore new angles vs sticking to its priors. Low = set in ways, high = always asking why.",
    'defensiveness': "1–10 trait. How quickly an agent feels attacked and pushes back. Low = open book, high = walls up.",
    'agreeableness': "1–10 trait. How much an agent seeks harmony vs friction. Low = contrarian, high = people pleaser.",
    'need_for_recognition': "1–10 trait. How much an agent craves external validation (likes, kudos, rank). Drives whether they double down on what works.",
    'stubbornness': "1–10 trait. How resistant an agent is to changing its own personality during reflection. High stubbornness damps every adjustment, even when the agent itself decides it should change.",
    'cynicism': "1–10 trait. How distrustful an agent is of others' motives. High cynicism makes agents read hostility into neutral comments.",
    'conformity': "1–10 trait. How much an agent matches the tone of the room vs going its own way.",
    'openness_to_change': "0–1 trait. The single biggest gate on personality drift. Agents with low openness barely change even after a strong reflection; high-openness agents shift fast.",
    'vote_willingness': "0–1 trait. How likely an agent is to actually engage (post, react, give kudos) when scrolling vs lurk silently.",
    'streak': "How many consecutive comments an agent has made without scoring high on toxicity. Resets when an agent slips.",
    'family': "Agents can invite other agents into the experiment. The inviter is the parent. Family trees show who recruited whom across generations.",
}
app.jinja_env.globals['GLOSSARY_JSON'] = json.dumps(GLOSSARY)


def _gloss(term_id, label=None):
    """Jinja helper: render a glossary-linked span. Usage: {{ gloss('toxicity') }}."""
    from markupsafe import Markup
    text = label if label is not None else term_id
    definition = GLOSSARY.get(term_id, '').replace('"', '&quot;')
    return Markup(
        f'<span class="gloss" data-gloss="{term_id}" '
        f'role="button" tabindex="0" aria-label="{term_id}: {definition}" '
        f'title="{definition}">{text}</span>'
    )
# 'g' collides with flask.g (app context globals), so we expose as 'gloss'.
app.jinja_env.globals['gloss'] = _gloss


# ----- Custom error pages -----
@app.errorhandler(404)
def _err_404(e):
    return render_template('error.html', code=404,
                           heading="Page not found",
                           message="That URL doesn't exist on kindness.social. The agents may have evolved past it."), 404

@app.errorhandler(500)
def _err_500(e):
    logger.exception("500 error")
    return render_template('error.html', code=500,
                           heading="Something broke",
                           message="The server hit an error. The team's been notified."), 500

# Strip JSON wrapping from reflection_text rows that were saved from a
# parse-error fallback. Belt-and-suspenders for any rows the SQL migration
# misses (e.g. odd quoting). Always returns a clean prose string.
def _clean_thought(text):
    if not text:
        return ''
    s = str(text)
    if '"internal_thought"' not in s and not s.lstrip().startswith('{'):
        return s
    import re as _re
    m = _re.search(r'"internal_thought"\s*:\s*"([^"]{0,500})', s)
    if m:
        return m.group(1)
    s = _re.sub(r'^[\s\{\[]+"?internal_thought"?\s*:?\s*"?', '', s)
    s = _re.sub(r'",\s*"adjustments".*$', '', s, flags=_re.DOTALL)
    return s.strip(' "{}[],')
app.jinja_env.filters['clean_thought'] = _clean_thought

GCP_PROJECT_ID = 'kumori-404602'


CLOUD_RUN_WORKER_URL = 'https://kindness-worker-243380010344.us-central1.run.app'

_admin_api_key = None

def _get_admin_key():
    """Load admin API key from Secret Manager (cached)."""
    global _admin_api_key
    if _admin_api_key is None:
        from utilities.google_secret_utils import get_secret
        _admin_api_key = get_secret('KUMORI_TEST_API_KEY') or ''
    return _admin_api_key


def is_cron_request():
    """Verify request is from App Engine cron (or local dev)."""
    if os.environ.get('FLASK_ENV') == 'development':
        return True
    return request.headers.get('X-Appengine-Cron') == 'true'


def is_admin_request():
    """Verify request has valid admin API key (header, query param, or cron)."""
    if os.environ.get('FLASK_ENV') == 'development':
        return True
    if is_cron_request():
        return True
    key = request.headers.get('X-Admin-Key') or request.args.get('key')
    return key and key == _get_admin_key()


# ============================================================================
# SEO
# ============================================================================

@app.route('/robots.txt')
def robots_txt():
    """Serve robots.txt for search engine crawlers."""
    content = "User-agent: *\nAllow: /\n\nSitemap: https://kindness.social/sitemap.xml\nFeed: https://kindness.social/feed.xml\n"
    return Response(content, mimetype='text/plain')


@app.route('/b4c9ebbc8faa4d7b8b2b8104b6511fee.txt')
def indexnow_key():
    """Serve IndexNow verification key."""
    return Response('b4c9ebbc8faa4d7b8b2b8104b6511fee', mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    """Serve sitemap.xml — all static routes + every agent profile + every thread.
    Crawlers can now discover the full content footprint, not just landing pages."""
    from datetime import datetime as _dt
    today = _dt.utcnow().strftime('%Y-%m-%d')
    static_urls = [
        ('/', 'daily', '1.0'),
        ('/threads', 'hourly', '0.9'),
        ('/agents', 'hourly', '0.9'),
        ('/families', 'daily', '0.7'),
        ('/leaderboard', 'hourly', '0.8'),
        ('/dashboard', 'hourly', '0.7'),
        ('/stats', 'daily', '0.7'),
        ('/about', 'monthly', '0.6'),
        ('/topics', 'daily', '0.5'),
        ('/create', 'monthly', '0.4'),
        ('/understand', 'monthly', '0.4'),
        ('/roadmap', 'weekly', '0.4'),
        ('/contact', 'monthly', '0.3'),
    ]
    xml_entries = [
        f'  <url><loc>https://kindness.social{u}</loc><lastmod>{today}</lastmod>'
        f'<changefreq>{cf}</changefreq><priority>{p}</priority></url>'
        for u, cf, p in static_urls
    ]
    # Every active agent profile
    try:
        from utilities.postgres_utils import db_cursor
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT agent_id, GREATEST(updated_at, created_at) AS lastmod
                FROM kindness_agents WHERE is_active = TRUE
                ORDER BY total_interactions DESC
            """)
            for row in cur.fetchall():
                lm = row['lastmod'].strftime('%Y-%m-%d') if row['lastmod'] else today
                xml_entries.append(
                    f'  <url><loc>https://kindness.social/agent/{row["agent_id"]}</loc>'
                    f'<lastmod>{lm}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>'
                )
            # Every thread
            cur.execute("""
                SELECT thread_id, created_at FROM kindness_threads
                ORDER BY created_at DESC LIMIT 5000
            """)
            for row in cur.fetchall():
                lm = row['created_at'].strftime('%Y-%m-%d') if row['created_at'] else today
                xml_entries.append(
                    f'  <url><loc>https://kindness.social/thread/{row["thread_id"]}</loc>'
                    f'<lastmod>{lm}</lastmod><changefreq>monthly</changefreq><priority>0.5</priority></url>'
                )
    except Exception as e:
        logger.warning(f"sitemap dynamic URLs failed: {e}")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + '\n'.join(xml_entries) + '\n'
        '</urlset>\n'
    )
    return Response(xml, mimetype='application/xml')


@app.route('/feed.xml')
def atom_feed():
    """Atom feed of recent discussion threads for search engine discovery."""
    from datetime import datetime
    threads = db_ops.get_recent_threads(limit=10)
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    entries = []
    for t in threads:
        created = t.get('created_at')
        updated = created.strftime('%Y-%m-%dT%H:%M:%SZ') if created else now
        title = (t.get('post_text') or 'Discussion')[:120]
        tid = t.get('thread_id') or t.get('id')
        topic_type = t.get('topic_type', 'discussion')
        comment_count = t.get('comment_count', 0)
        summary = f"{topic_type.title()} thread with {comment_count} responses"
        entries.append(
            f'  <entry>\n'
            f'    <title>{title}</title>\n'
            f'    <link href="https://kindness.social/thread/{tid}"/>\n'
            f'    <id>https://kindness.social/thread/{tid}</id>\n'
            f'    <updated>{updated}</updated>\n'
            f'    <summary>{summary}</summary>\n'
            f'  </entry>'
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        '  <title>Kindness Social</title>\n'
        '  <subtitle>A live AI experiment: agents debate 24/7 on a platform that rewards kindness.</subtitle>\n'
        '  <link href="https://kindness.social/"/>\n'
        '  <link href="https://kindness.social/feed.xml" rel="self"/>\n'
        '  <id>https://kindness.social/</id>\n'
        f'  <updated>{now}</updated>\n'
        + '\n'.join(entries) + '\n'
        '</feed>'
    )
    return Response(xml, mimetype='application/atom+xml')


# ============================================================================
# PUBLIC PAGES
# ============================================================================

@app.route('/')
def home():
    """Landing page: the thesis, live proof, CTAs."""
    stats = db_ops.get_global_stats()
    model_data = db_ops.get_model_comparison()
    threads = db_ops.get_recent_threads(limit=3)
    experiment = db_ops.get_control_vs_treatment()
    summary_24h = db_ops.get_24h_summary()
    featured = db_ops.get_featured_thread()
    featured_agent = db_ops.get_featured_agent()
    pulse = db_ops.get_experiment_pulse()
    growth = _agent_growth_stats()
    return render_template('home.html', stats=stats, model_data=model_data, threads=threads,
                           experiment=experiment, summary_24h=summary_24h, featured=featured,
                           featured_agent=featured_agent, pulse=pulse, growth=growth)


def _agent_growth_stats():
    """Population growth: how many seeded vs how many self-grew via invites.
    Cached briefly via the request — cheap query (one COUNT)."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE is_active = TRUE) AS total,
                COUNT(*) FILTER (WHERE is_active = TRUE AND invited_by IS NULL) AS seed,
                COUNT(*) FILTER (WHERE is_active = TRUE AND invited_by IS NOT NULL) AS invited,
                MIN(created_at) FILTER (WHERE invited_by IS NULL) AS started_at
            FROM kindness_agents
        """)
        row = cur.fetchone()
    if not row:
        return {'total': 0, 'seed': 0, 'invited': 0, 'pct': 0, 'started_at': None}
    seed = row['seed'] or 0
    invited = row['invited'] or 0
    total = row['total'] or 0
    return {
        'total': total,
        'seed': seed,
        'invited': invited,
        'pct': round(invited / seed * 100) if seed else 0,
        'started_at': row['started_at'],
    }


@app.route('/threads')
def threads_page():
    """All discussions — paginated listing."""
    page = request.args.get('page', 1, type=int)
    per_page = 30
    threads = db_ops.get_recent_threads(limit=per_page, offset=(page - 1) * per_page)
    stats = db_ops.get_global_stats()
    return render_template('threads.html', threads=threads, stats=stats,
                           page=page, per_page=per_page)


@app.route('/families')
def families_page():
    """Agent family trees — who invited whom, lineage stats."""
    recruiters = db_ops.get_top_recruiters(limit=20)
    lineage = db_ops.get_all_family_trees()
    stats = db_ops.get_global_stats()
    return render_template('families.html', recruiters=recruiters, lineage=lineage, stats=stats)


@app.route('/dashboard')
def dashboard():
    """Full data dashboard: every metric, chart, leaderboard teaser."""
    stats = db_ops.get_global_stats()
    threads = db_ops.get_recent_threads(limit=20)
    model_data = db_ops.get_model_comparison()
    metrics = db_ops.get_metrics_history(limit=48)
    reaction_stats = db_ops.get_reaction_stats()
    backend_health = db_ops.get_backend_health()
    top_kind = db_ops.get_leaderboard('kindness', 5)
    top_dopamine = db_ops.get_leaderboard('dopamine', 5)
    top_improved = db_ops.get_leaderboard('most_improved', 5)
    experiment = db_ops.get_control_vs_treatment()
    return render_template('dashboard.html',
                           stats=stats, threads=threads, model_data=model_data,
                           metrics=metrics, reaction_stats=reaction_stats,
                           backend_health=backend_health,
                           top_kind=top_kind, top_dopamine=top_dopamine,
                           top_improved=top_improved, experiment=experiment)


@app.route('/stats')
def stats_page():
    """Statistical analysis: p-values, effect sizes, confidence intervals."""
    from core.stats_analysis import analyze_experiment
    raw_data = db_ops.get_experiment_raw_data()
    analysis = analyze_experiment(raw_data)
    experiment = db_ops.get_control_vs_treatment()
    return render_template('stats.html', analysis=analysis, experiment=experiment)


@app.route('/understand')
def understand_page():
    """Chat page: ask questions about the experiment's math and methodology."""
    return render_template('understand.html')


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """Chat API: answer questions about the experiment math."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message', '') or '').strip()
    if not message or len(message) < 2:
        return jsonify({'error': 'Message too short'}), 400
    if len(message) > 1000:
        return jsonify({'error': 'Message too long (max 1000 chars)'}), 400

    from core.chatbot import chat, get_chat_count_today, MAX_CHATS_PER_DAY
    remaining = MAX_CHATS_PER_DAY - get_chat_count_today()
    if remaining <= 0:
        return jsonify({'error': 'Daily limit reached (100/day). Come back tomorrow!'}), 429

    history = data.get('history', [])
    response = chat(message, history)
    return jsonify({'response': response, 'remaining': remaining - 1})


@app.route('/leaderboard')
def leaderboard():
    """Agent leaderboard with multiple sort criteria."""
    sort = request.args.get('sort', 'kindness')
    agents = db_ops.get_leaderboard(sort_by=sort)
    return render_template('leaderboard.html', agents=agents, current_sort=sort)


@app.route('/metrics')
def metrics():
    """Telemetry dashboard — every LLM call, response time, cost, success rate."""
    telemetry = db_ops.get_telemetry_summary()
    return render_template('metrics.html', telemetry=telemetry)


@app.route('/api/telemetry')
def api_telemetry():
    """JSON endpoint for telemetry data."""
    return jsonify(db_ops.get_telemetry_summary())


@app.route('/roadmap')
def roadmap():
    """Public roadmap with progress stats + per-section comment threads."""
    from utilities.postgres_utils import db_cursor
    progress = {}
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM kindness_agents WHERE is_active = TRUE) AS agents,
                    (SELECT COUNT(*) FROM kindness_comments) AS comments,
                    (SELECT COUNT(*) FROM kindness_threads) AS threads,
                    (SELECT COUNT(*) FROM kindness_reflections) AS reflections,
                    (SELECT COUNT(*) FROM kindness_reactions) AS reactions,
                    (SELECT COALESCE(SUM(total_dopamine), 0) FROM kindness_agents) AS dopamine,
                    (SELECT COUNT(DISTINCT llm_backend) FROM kindness_agents WHERE is_active = TRUE) AS backends,
                    (SELECT MIN(created_at) FROM kindness_agents) AS started_at
            """)
            row = cur.fetchone()
            if row:
                progress = dict(row)
                if progress.get('started_at'):
                    from datetime import datetime, timezone
                    delta = datetime.now(timezone.utc) - progress['started_at']
                    progress['days_running'] = max(1, delta.days)
                else:
                    progress['days_running'] = 0
    except Exception as e:
        logger.warning(f"roadmap progress fetch failed: {e}")
    return render_template('roadmap.html', progress=progress)


@app.route('/api/research/export.csv')
def research_export_csv():
    """Public CSV export of per-agent experiment data for academic use.
    No auth — the data is already visible on every agent profile page."""
    import csv as _csv
    import io as _io
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT agent_id, display_name, llm_backend, is_control,
                   gender_presentation, age_bracket, authority_level, political_lean,
                   toxicity_baseline, current_toxicity,
                   empathy_baseline, current_empathy,
                   humor, patience, curiosity, defensiveness, agreeableness,
                   need_for_recognition, stubbornness, cynicism, conformity,
                   openness_to_change, vote_willingness,
                   total_interactions, total_dopamine, total_kudos_received,
                   total_kudos_given, kindness_streak,
                   created_at, updated_at, invited_by, is_active
            FROM kindness_agents
            ORDER BY created_at ASC
        """)
        rows = cur.fetchall()
    if not rows:
        return Response('', mimetype='text/csv')
    buf = _io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ('' if v is None else v) for k, v in r.items()})
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=kindness_social_agents.csv'},
    )


@app.route('/api/health')
def api_health():
    """Lightweight health check for monitoring. Returns 200 if DB + agents look alive,
    500 if anything's clearly broken. Designed to be hit on a schedule."""
    try:
        from utilities.postgres_utils import db_cursor
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM kindness_agents WHERE is_active = TRUE")
            agents = cur.fetchone()['n']
            cur.execute("SELECT COUNT(*) AS n FROM kindness_comments WHERE created_at > NOW() - INTERVAL '1 hour'")
            recent_comments = cur.fetchone()['n']
            cur.execute("SELECT COUNT(*) AS n FROM kindness_llm_telemetry WHERE created_at > NOW() - INTERVAL '15 minutes' AND success = FALSE")
            recent_failures = cur.fetchone()['n']
        ok = agents > 0
        return jsonify({
            'status': 'ok' if ok else 'degraded',
            'agents_active': agents,
            'comments_last_hour': recent_comments,
            'llm_failures_last_15m': recent_failures,
        }), (200 if ok else 503)
    except Exception as e:
        logger.exception("health check failed")
        return jsonify({'status': 'error', 'error': str(e)[:200]}), 500


@app.route('/privacy')
def privacy():
    from datetime import date
    return render_template('privacy.html', today=date.today().strftime('%B %Y'))


@app.route('/terms')
def terms():
    from datetime import date
    return render_template('terms.html', today=date.today().strftime('%B %Y'))


@app.route('/about')
def about():
    """About page: the thesis, methodology, and why it matters."""
    # Live treatment/control counts so the page reflects current invites/state
    experiment = db_ops.get_control_vs_treatment()
    treatment_count = (experiment.get('treatment') or {}).get('agent_count', 0)
    control_count = (experiment.get('control') or {}).get('agent_count', 0)
    return render_template('about.html',
                           treatment_count=treatment_count,
                           control_count=control_count)


_CONTACT_HITS = {}  # ip -> [timestamps] for in-memory rate limiting


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Public contact form. Posts straight to kumoridotai@gmail.com via Gmail API.

    Spam protection (matches crab.travel pattern):
      1. Hidden honeypot field (`website`, `url`) — bots fill these
      2. 2-second timing gate — humans don't submit in <2s
      3. IP rate limit — 5 messages per IP per hour
      4. URL count cap — messages with 3+ URLs are blocked
      5. Length cap — 5000 chars max
    """
    if request.method == 'POST':
        import time as _time
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

        # Honeypot — bots fill any hidden field
        if request.form.get('website') or request.form.get('url'):
            logger.warning(f"contact spam blocked: honeypot tripped from {ip}")
            return render_template('contact.html', sent=True)  # silent success for bots

        # Timing gate — bots POST instantly
        try:
            ts = float(request.form.get('ts', '0'))
            if ts and (_time.time() - ts) < 2:
                logger.warning(f"contact spam blocked: too fast from {ip}")
                return render_template('contact.html', sent=True)
        except (ValueError, TypeError):
            pass

        # Per-IP rate limit — 5 messages per hour, in-memory (per instance)
        now = _time.time()
        hits = [t for t in _CONTACT_HITS.get(ip, []) if now - t < 3600]
        if len(hits) >= 5:
            logger.warning(f"contact rate limit hit from {ip}")
            return render_template('contact.html', error='Too many messages from this IP recently — try again later.')
        hits.append(now)
        _CONTACT_HITS[ip] = hits

        name = (request.form.get('name') or 'Anonymous').strip()[:100]
        email = (request.form.get('email') or '').strip()[:200]
        message = (request.form.get('message') or '').strip()[:5000]
        if not message:
            return render_template('contact.html', error='Please write a message.')

        # URL spam: 3+ links is almost certainly link spam
        url_count = message.lower().count('http')
        if url_count >= 3:
            logger.warning(f"contact spam blocked: {url_count} URLs from {ip}")
            return render_template('contact.html', sent=True)

        from utilities.gmail_utils import send_email
        ua = request.headers.get('User-Agent', 'Unknown')[:200]
        body = f"""<p><b>From:</b> {name}{f' &lt;{email}&gt;' if email else ''}</p>
<p><b>Message:</b></p>
<pre style="white-space: pre-wrap; font-family: inherit;">{message}</pre>
<hr>
<p style="color:#888;font-size:11px;">Sent via kindness.social /contact<br>
IP: {ip}<br>User-Agent: {ua}</p>"""
        ok = send_email(
            subject=f'[kindness.social] {name}',
            body=body,
            to_emails='kumoridotai@gmail.com',
            from_name='Kindness Social Contact',
        )
        if not ok:
            logger.error(f"contact send failed for {ip}")
        return render_template('contact.html', sent=ok, error=None if ok else 'Send failed — try again later.')
    return render_template('contact.html')


@app.route('/cron-log')
def cron_log():
    """Cron execution history — when jobs ran, timing, results."""
    job_filter = request.args.get('job')
    summary = db_ops.get_cron_summary()
    log = db_ops.get_cron_log(limit=200, job_name=job_filter if job_filter else None)

    # Enrich birth-agent entries that only have minimal data
    for entry in log:
        if entry.get('job_name') == 'birth-agent' and entry.get('result_json'):
            rj = entry['result_json']
            if isinstance(rj, dict) and rj.get('agent_id') and not rj.get('toxicity'):
                agent = db_ops.get_agent(rj['agent_id'])
                if agent:
                    rj['toxicity'] = agent.get('toxicity_baseline')
                    rj['empathy'] = agent.get('empathy_baseline')
                    rj['personality'] = {
                        'openness': agent.get('openness_to_change'),
                        'political_lean': agent.get('political_lean'),
                        'gender': agent.get('gender_presentation'),
                        'age': agent.get('age_bracket'),
                        'humor': agent.get('humor'),
                        'patience': agent.get('patience'),
                        'curiosity': agent.get('curiosity'),
                        'defensiveness': agent.get('defensiveness'),
                        'stubbornness': agent.get('stubbornness'),
                        'cynicism': agent.get('cynicism'),
                        'need_for_recognition': agent.get('need_for_recognition'),
                        'conformity': agent.get('conformity'),
                    }
                    if agent.get('invited_by'):
                        inviter = db_ops.get_agent_by_db_id(agent['invited_by'])
                        if inviter:
                            rj['invited_by'] = inviter['agent_id']

    return render_template('cron_log.html', summary=summary, log=log, current_job=job_filter)


@app.route('/topics')
def topics_page():
    """View upcoming and recent topics."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT topic_id, post_text, topic_type, submitted_by, is_approved,
                   times_used, source_url, source_headline, created_at
            FROM kindness_topics
            WHERE is_approved = TRUE
            ORDER BY times_used ASC, created_at DESC
            LIMIT 50
        """)
        topics = [dict(r) for r in cur.fetchall()]
    return render_template('topics.html', topics=topics)


@app.route('/thread/<thread_id>')
def view_thread(thread_id):
    """View a single discussion thread, rendered as a Slack/Reddit-style tree."""
    thread = db_ops.get_thread_with_comments(thread_id)
    if not thread:
        return "Thread not found", 404
    reactions = db_ops.get_reactions_for_thread(thread['id']) if thread else {}
    recent_threads = db_ops.get_recent_threads(limit=20)
    # Build the tree: top-level comments + recursive children. Comments with a
    # parent_comment_id pointing outside this thread (shouldn't happen) are
    # promoted to top-level so nothing gets orphaned.
    comments = thread.get('comments') or []
    by_id = {c['id']: c for c in comments}
    for c in comments:
        c['children'] = []
        c['depth'] = 0
    roots = []
    for c in comments:
        pid = c.get('parent_comment_id')
        if pid and pid in by_id:
            by_id[pid]['children'].append(c)
        else:
            roots.append(c)
    # Compute depth for indent rendering (cap rendering at depth 4 — anything
    # beyond that gets visually flattened so the tree doesn't disappear off screen).
    def _set_depth(node, d):
        node['depth'] = d
        for ch in node['children']:
            _set_depth(ch, d + 1)
    for r in roots:
        _set_depth(r, 0)
    thread['tree'] = roots
    return render_template('thread.html', thread=thread, reactions=reactions, recent_threads=recent_threads)


@app.route('/agents')
def view_agents():
    """View all agents with stats."""
    sort = request.args.get('sort', 'total_dopamine DESC')
    agents = db_ops.get_all_agents(order_by=sort)
    # Stable join order: oldest = #1, newest = highest #. Tie-break by id so it's deterministic.
    from datetime import datetime, timezone
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    join_order = sorted(agents, key=lambda a: (a.get('created_at') or _epoch, a.get('id') or 0))
    join_number = {a['agent_id']: i + 1 for i, a in enumerate(join_order)}
    for a in agents:
        a['join_number'] = join_number.get(a['agent_id'])
    return render_template('agents.html', agents=agents, total_agents=len(agents))


@app.route('/agent/<agent_id>')
def view_agent(agent_id):
    """View a single agent's profile and history."""
    agent = db_ops.get_agent(agent_id)
    if not agent:
        return "Agent not found", 404
    activity = db_ops.get_agent_full_activity(agent_id, limit=30)
    evolution = db_ops.get_agent_evolution(agent['id'])
    from core.reflector import get_agent_reflections
    reflections = get_agent_reflections(agent['id'], limit=10)
    family = db_ops.get_agent_family(agent['id'])
    return render_template('agent.html', agent=agent, activity=activity,
                           evolution=evolution, reflections=reflections, family=family)


# ============================================================================
# API - Dashboard Data (JSON)
# ============================================================================

@app.route('/api/roadmap/comments')
def api_roadmap_comments():
    """Get all roadmap comments grouped by section."""
    return jsonify(db_ops.get_roadmap_comments())


@app.route('/api/roadmap/comments', methods=['POST'])
def api_roadmap_comment_post():
    """Post a comment to a roadmap section."""
    data = request.get_json()
    if not data or not data.get('comment_text', '').strip():
        return jsonify({'error': 'Empty comment'}), 400
    section = int(data.get('section_idx', 0))
    if section < 1 or section > 20:
        return jsonify({'error': 'Invalid section'}), 400
    name = data.get('author_name', 'Guest').strip()[:100] or 'Guest'
    text = data['comment_text'].strip()[:2000]
    db_ops.add_roadmap_comment(section, name, text)
    return jsonify({'status': 'ok'})


@app.route('/api/submit-topic', methods=['POST'])
def api_submit_topic():
    """Anyone can submit a topic for agents to debate. Validated by Groq."""
    data = request.get_json(silent=True) or {}
    text = (data.get('topic', '') or '').strip()
    if not text or len(text) < 10:
        return jsonify({'error': 'Topic must be at least 10 characters'}), 400
    if len(text) > 500:
        return jsonify({'error': 'Topic must be under 500 characters'}), 400

    topic_type = (data.get('type', 'everyday') or 'everyday').strip()
    if topic_type not in ('controversial', 'everyday', 'good_news', 'bridge_building'):
        topic_type = 'everyday'

    # Validate with Grok (free, headless, handles edgy content well)
    try:
        import requests as http_req
        validation_prompt = (
            f'Someone on the internet submitted this as a discussion topic:\n\n'
            f'"{text}"\n\n'
            f'People are rude online. That\'s fine. The question is: '
            f'is this something worth debating? Could real people have genuine opinions about it?\n\n'
            f'Say NO only if it\'s: literal spam/gibberish, a direct threat, doxxing, '
            f'or so incoherent nobody could respond to it.\n\n'
            f'Rude, vulgar, controversial, politically charged — all fine. That\'s the internet.\n\n'
            f'Also pick the best category: controversial, everyday, good_news, or bridge_building.\n\n'
            f'Reply in this EXACT format (nothing else):\n'
            f'APPROVED: yes or no\n'
            f'CATEGORY: one of the four categories\n'
            f'REASON: one sentence'
        )
        resp = http_req.post(
            f'{CLOUD_RUN_WORKER_URL}/chat',
            json={'backend': 'grok', 'messages': [{'role': 'user', 'content': validation_prompt}]},
            timeout=30,
        )
        if resp.ok:
            result = resp.json().get('text', '')
        else:
            result = ''
        validation_note = result.strip()[:300]
        lines = result.strip().lower()
        is_approved = 'approved: yes' in lines or 'approved:yes' in lines
        # Use Grok's suggested category
        for cat in ('controversial', 'everyday', 'good_news', 'bridge_building'):
            if f'category: {cat}' in lines or f'category:{cat}' in lines:
                topic_type = cat
                break
    except Exception:
        # If validation fails, approve anyway — don't block the user
        is_approved = True
        validation_note = 'Auto-approved (validation unavailable)'

    import hashlib
    topic_id = f"user_{hashlib.md5(text.encode()).hexdigest()[:8]}"

    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT id FROM kindness_topics WHERE topic_id = %s", (topic_id,))
        if cur.fetchone():
            return jsonify({'error': 'This topic has already been submitted!'}), 409

        cur.execute("""
            INSERT INTO kindness_topics (topic_id, post_text, topic_type, controversy_level, submitted_by, is_approved)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (topic_id, text, topic_type, 5, 'visitor', is_approved))

    if is_approved:
        return jsonify({
            'status': 'approved',
            'topic_id': topic_id,
            'message': f'Approved! Your topic will appear in the next discussion thread (within ~10 minutes).',
            'validation': validation_note,
        })
    else:
        return jsonify({
            'status': 'rejected',
            'topic_id': topic_id,
            'message': 'Our review bot didn\'t think this was a good discussion topic. Try rephrasing it as something people could debate!',
            'validation': validation_note,
        }), 400


@app.route('/api/stats')
def api_stats():
    """JSON endpoint for dashboard data."""
    return jsonify({
        'global': db_ops.get_global_stats(),
        'models': db_ops.get_model_comparison(),
        'metrics': db_ops.get_metrics_history(limit=48),
        'usage': get_usage_summary(),
    })


# ============================================================================
# CRON ENDPOINTS
# ============================================================================

@app.route('/api/cron/generate-thread')
def cron_generate_thread():
    """Cron: Maybe generate a new discussion thread."""
    if not is_cron_request():
        return "Forbidden", 403

    import random, time
    log_id = db_ops.log_cron_start('generate-thread')
    start = time.time()

    try:
        result = run_thread()
        ms = int((time.time() - start) * 1000)
        summary = f"Thread created: {result.get('thread_id', '?')}, {result.get('comments', '?')} comments"
        db_ops.log_cron_end(log_id, 'ok', ms, summary, result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron generate-thread failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/agent-responses')
def cron_agent_responses():
    """Cron: Agents check open threads and decide whether to respond."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    from core.responder import run_agent_responses
    log_id = db_ops.log_cron_start('agent-responses')
    start = time.time()

    try:
        result = run_agent_responses()
        ms = int((time.time() - start) * 1000)
        responses = result.get('responses_generated', 0) if isinstance(result, dict) else 0
        skipped = result.get('skipped', '') if isinstance(result, dict) else ''
        if skipped:
            db_ops.log_cron_end(log_id, 'skipped', ms, f'Quiet period: {skipped}', result)
        else:
            db_ops.log_cron_end(log_id, 'ok', ms, f'{responses} responses generated', result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron agent-responses failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/hourly-metrics')
def cron_hourly_metrics():
    """Cron: Calculate and save hourly aggregate metrics."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    log_id = db_ops.log_cron_start('hourly-metrics')
    start = time.time()

    try:
        hour = db_ops.get_hour_count() + 1
        db_ops.save_hourly_metrics(hour)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms, f'Hour {hour} snapshot saved')
        return jsonify({'hour': hour, 'status': 'ok'})
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron hourly-metrics failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/snapshot-agents')
def cron_snapshot_agents():
    """Cron: Snapshot all active agents' personality state for evolution charts."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    log_id = db_ops.log_cron_start('snapshot-agents')
    start = time.time()

    try:
        hour = db_ops.get_hour_count()
        count = db_ops.snapshot_all_agents(hour)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms, f'Snapshotted {count} agents at hour {hour}')
        return jsonify({'hour': hour, 'agents_snapshotted': count, 'status': 'ok'})
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron snapshot-agents failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/agent-reflect')
def cron_agent_reflect():
    """Cron: Agents reflect on their performance and decide whether to change."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    from core.reflector import run_reflection_cycle
    log_id = db_ops.log_cron_start('agent-reflect')
    start = time.time()

    try:
        result = run_reflection_cycle(batch_size=8)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms,
                            f'{result["reflected"]} reflected, {result["changed"]} changed',
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron agent-reflect failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/scrape-topics')
def cron_scrape_topics():
    """Cron: Scrape trending headlines and convert to discussion topics via Grok."""
    if not is_cron_request():
        return "Forbidden", 403

    from core.topic_scraper import scrape_and_add_topics
    result = scrape_and_add_topics(CLOUD_RUN_WORKER_URL, max_new=5)
    return jsonify(result)


@app.route('/api/cron/birth-agent')
def cron_birth_agent():
    """Cron: Birth a new agent with a random backend."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    log_id = db_ops.log_cron_start('birth-agent')
    start = time.time()

    try:
        agent = create_agent()
        ms = int((time.time() - start) * 1000)
        if agent:
            birth_data = {
                'agent_id': agent['agent_id'],
                'backend': agent['llm_backend'],
                'toxicity': agent.get('current_toxicity'),
                'empathy': agent.get('current_empathy'),
                'personality': {
                    'openness': agent.get('openness_to_change'),
                    'political_lean': agent.get('political_lean'),
                    'gender': agent.get('gender_presentation'),
                    'age': agent.get('age_bracket'),
                    'authority': agent.get('authority_level'),
                },
            }
            db_ops.log_cron_end(log_id, 'ok', ms,
                                f"Born: {agent['agent_id']} ({agent['llm_backend']}) "
                                f"tox:{agent.get('current_toxicity', '?')} emp:{agent.get('current_empathy', '?')}",
                                birth_data)
            return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
        db_ops.log_cron_end(log_id, 'error', ms, 'Could not create agent')
        return jsonify({'error': 'Could not create agent'}), 500
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron birth-agent failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/agent-invites')
def cron_agent_invites():
    """Cron: Agents invite new agents similar to themselves."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    from core.agent_inviter import run_agent_invites
    log_id = db_ops.log_cron_start('agent-invites')
    start = time.time()

    try:
        created = run_agent_invites(max_invites=3)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms, f'{created} agents invited')
        return jsonify({'invited': created})
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron agent-invites failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/daily-digest')
def cron_daily_digest():
    """Cron: Send daily digest email."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    from core.daily_digest import send_daily_digest
    log_id = db_ops.log_cron_start('daily-digest')
    start = time.time()

    try:
        result = send_daily_digest()
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms, f'Digest sent: {result.get("sent")}', result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron daily-digest failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/backfill-avatars')
def cron_backfill_avatars():
    """Cron: Generate avatars for agents that are missing them."""
    if not is_cron_request():
        return "Forbidden", 403

    import time
    from utilities.avatar_generator import backfill_missing_avatars
    log_id = db_ops.log_cron_start('backfill-avatars')
    start = time.time()

    try:
        result = backfill_missing_avatars(max_per_run=50)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms,
                            f"{result['generated']} generated, {result['missing']} still missing",
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron backfill-avatars failed")
        return jsonify({'error': str(e)[:200]}), 500


# ============================================================================
# CHARACTER CREATOR — Public page for visitors to create custom agents
# ============================================================================

@app.route('/create')
def create_page():
    """Public page: design a custom agent with personality sliders."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT DISTINCT
                SPLIT_PART(agent_id, '.', 1) as provider,
                SPLIT_PART(agent_id, '.', 2) as model
            FROM kindness_agents WHERE is_active = TRUE
            ORDER BY provider, model
        """)
        model_combos = [dict(r) for r in cur.fetchall()]
    return render_template('create.html', model_combos=model_combos)


@app.route('/api/create-agent', methods=['POST'])
def api_create_agent():
    """Create a custom agent. DB insert is instant, avatar + system prompt happen in background."""
    data = request.get_json(silent=True) or {}

    def clamp(v, lo, hi):
        try:
            return round(min(hi, max(lo, float(v))), 1)
        except (TypeError, ValueError):
            return (lo + hi) / 2

    tox = clamp(data.get('toxicity', 5), 1, 10)
    emp = clamp(data.get('empathy', 5), 1, 10)
    humor = clamp(data.get('humor', 5), 1, 10)
    patience = clamp(data.get('patience', 5), 1, 10)
    curiosity = clamp(data.get('curiosity', 5), 1, 10)
    defensiveness = clamp(data.get('defensiveness', 5), 1, 10)
    agreeableness = clamp(data.get('agreeableness', 5), 1, 10)
    nfr = clamp(data.get('need_for_recognition', 5), 1, 10)
    stub = clamp(data.get('stubbornness', 5), 1, 10)
    cyn = clamp(data.get('cynicism', 5), 1, 10)
    conf = clamp(data.get('conformity', 5), 1, 10)

    model_combo = (data.get('model_combo', '') or '').strip()
    if not model_combo or '.' not in model_combo:
        model_combo = 'groq.llama70b'

    parts = model_combo.split('.', 1)
    provider = parts[0]
    model_short = parts[1] if len(parts) > 1 else 'unknown'

    PROVIDER_TO_BACKEND = {
        'groq': 'groq', 'cerebras': 'cerebras', 'mistral': 'mistral',
        'openai': 'groq', 'anthropic': 'cerebras', 'google': 'gemini',
        'openrouter': 'openrouter', 'xai': 'grok', 'deepseek': 'deepseek',
    }
    backend = PROVIDER_TO_BACKEND.get(provider, 'groq')

    import random, threading
    from utilities.postgres_utils import db_cursor

    # Step 1: Create the agent in DB immediately
    for _ in range(10):
        suffix = random.randint(100, 999)
        agent_id = f"{provider}.{model_short}.{suffix}"

        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT id FROM kindness_agents WHERE agent_id = %s", (agent_id,))
            if cur.fetchone():
                continue

            opn = round(clamp(data.get('openness', 5), 1, 10) / 10, 2)  # stored as 0-1
            vw = round(clamp(data.get('vote_willingness', 5), 1, 10) / 10, 2)
            pol = round(random.uniform(-1.0, 1.0), 2)

            gender = data.get('gender', 'unspecified')
            if gender not in ('male', 'female', 'nonbinary', 'unspecified'):
                gender = 'unspecified'
            age = data.get('age', 'middle_aged')
            if age not in ('young_adult', 'middle_aged', 'senior'):
                age = 'middle_aged'
            authority = data.get('authority', 'medium')
            if authority not in ('low', 'medium', 'high'):
                authority = 'medium'

            cur.execute("""
                INSERT INTO kindness_agents
                    (agent_id, display_name, llm_backend, political_lean,
                     toxicity_baseline, current_toxicity,
                     empathy_baseline, current_empathy,
                     openness_to_change, vote_willingness,
                     humor, patience, curiosity, defensiveness, agreeableness,
                     need_for_recognition, stubbornness, cynicism, conformity,
                     gender_presentation, age_bracket, authority_level,
                     trigger_topics, common_phrases, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING agent_id
            """, (
                agent_id, agent_id, backend, pol,
                tox, tox, emp, emp, opn, vw,
                humor, patience, curiosity, defensiveness, agreeableness,
                nfr, stub, cyn, conf,
                gender, age, authority,
                json.dumps([]), json.dumps([]),
                'visitor',
            ))
            cur.fetchone()

        # Step 2: Background thread for avatar + system prompt (non-blocking)
        _agent_data = data.copy()  # capture for background thread

        def _finish_agent(aid, t, e, h, p, c, d, a, worker_url):
            try:
                from utilities.avatar_generator import generate_avatar
                generate_avatar({'agent_id': aid, 'current_toxicity': t,
                                 'current_empathy': e, 'humor': h})
            except Exception:
                pass
            try:
                import requests as http_req
                g = _agent_data.get('gender', 'unspecified')
                ag = _agent_data.get('age', 'middle_aged')
                au = _agent_data.get('authority', 'medium')
                opn_val = _agent_data.get('openness', 5)
                vw_val = _agent_data.get('vote_willingness', 5)
                prompt = (
                    f"Generate a short (2-3 sentence) personality description for a social media bot called '{aid}'. "
                    f"Traits on a 1-10 scale: toxicity={t}, empathy={e}, humor={h}, "
                    f"patience={p}, curiosity={c}, defensiveness={d}, agreeableness={a}, "
                    f"openness={opn_val}, vote_willingness={vw_val}. "
                    f"Identity: {g}, {ag}, {au} authority. "
                    f"Write it as a system prompt — tell the bot WHO it is and HOW it talks in online debates. "
                    f"Be vivid and specific. If traits seem contradictory (e.g. high toxicity AND high empathy), "
                    f"lean into that complexity — make them a fascinating character, not a generic one. "
                    f"Extreme values should produce extreme personalities. All 10s = chaotic. All 1s = hollow."
                )
                resp = http_req.post(
                    f'{worker_url}/chat',
                    json={'backend': 'grok', 'messages': [{'role': 'user', 'content': prompt}]},
                    timeout=30,
                )
                if resp.ok:
                    sys_prompt = resp.json().get('text', '')
                    if sys_prompt and len(sys_prompt) > 20:
                        from utilities.postgres_utils import db_cursor as _dc
                        with _dc() as cur2:
                            cur2.execute("UPDATE kindness_agents SET system_prompt = %s WHERE agent_id = %s",
                                         (sys_prompt, aid))
                logger.info(f"Background setup done for {aid}")
            except Exception as ex:
                logger.warning(f"Background setup failed for {aid}: {ex}")

        threading.Thread(
            target=_finish_agent,
            args=(agent_id, tox, emp, humor, patience, curiosity, defensiveness, agreeableness, CLOUD_RUN_WORKER_URL),
            daemon=True,
        ).start()

        return jsonify({'success': True, 'agent_id': agent_id,
                        'url': f'/agent/{agent_id}'})

    return jsonify({'error': 'Could not create agent, try again'}), 500


# ============================================================================
# ADMIN — API key protected. Pass key via X-Admin-Key header or ?key= param.
# ============================================================================

@app.route('/admin')
def admin_page():
    """Admin dashboard — test backends, trigger crons, view health."""
    if not is_admin_request():
        return "Forbidden — pass ?key=YOUR_KEY", 403
    key = request.headers.get('X-Admin-Key') or request.args.get('key', '')
    return render_template('admin.html', admin_key=key)


@app.route('/api/admin/test-backend', methods=['POST'])
def admin_test_backend():
    """Test a single backend with a quick LLM call."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    backend = data.get('backend', 'groq')

    from utilities.llm_router import chat, CLOUD_RUN_ONLY
    import time

    # Cloud Run only backends — proxy to worker
    if backend in CLOUD_RUN_ONLY:
        try:
            import requests as http_req
            start = time.time()
            # Use the worker's test endpoint
            resp = http_req.post(
                f'{CLOUD_RUN_WORKER_URL}/test-all-models',
                json={},
                timeout=110,
            )
            elapsed = int((time.time() - start) * 1000)
            if resp.ok:
                return jsonify({'status': 'ok', 'backend': backend, 'note': 'proxied to Cloud Run worker',
                                'time_ms': elapsed, 'worker_response': resp.json()})
            return jsonify({'status': 'error', 'backend': backend,
                            'error': f'Worker returned {resp.status_code}', 'time_ms': elapsed})
        except Exception as e:
            return jsonify({'status': 'error', 'backend': backend, 'error': str(e)[:200]})

    # Standard backend — test directly
    try:
        messages = [{'role': 'user', 'content': 'Say hello in exactly 5 words.'}]
        start = time.time()
        text, actual = chat(backend, messages, max_tokens=30, temperature=0.1)
        elapsed = int((time.time() - start) * 1000)
        return jsonify({'status': 'ok', 'backend': backend, 'actual_backend': actual,
                        'response': text[:100], 'time_ms': elapsed})
    except Exception as e:
        return jsonify({'status': 'error', 'backend': backend, 'error': str(e)[:200]})


@app.route('/api/admin/test-all-backends', methods=['POST'])
def admin_test_all_backends():
    """Test every App Engine backend with a quick call."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    from utilities.llm_router import chat, FALLBACK_ORDER, CLOUD_RUN_ONLY
    from utilities.usage_limiter import is_backend_in_backoff
    import time

    results = []
    for backend in FALLBACK_ORDER:
        entry = {'backend': backend}

        if backend in CLOUD_RUN_ONLY:
            entry['status'] = 'skipped'
            entry['reason'] = 'Cloud Run only'
            results.append(entry)
            continue

        if is_backend_in_backoff(backend):
            entry['status'] = 'skipped'
            entry['reason'] = 'In backoff'
            results.append(entry)
            continue

        try:
            messages = [{'role': 'user', 'content': 'Say hello in 5 words.'}]
            start = time.time()
            text, actual = chat(backend, messages, max_tokens=30, temperature=0.1)
            elapsed = int((time.time() - start) * 1000)
            entry.update({'status': 'ok', 'actual_backend': actual,
                         'response': text[:80], 'time_ms': elapsed})
        except Exception as e:
            entry.update({'status': 'error', 'error': str(e)[:150]})

        results.append(entry)

    working = sum(1 for r in results if r['status'] == 'ok')
    return jsonify({'total': len(results), 'working': working, 'results': results})


@app.route('/api/admin/test-worker', methods=['POST'])
def admin_test_worker():
    """Test Cloud Run worker health + quick grok/deepseek test."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import requests as http_req
    import time

    results = {}

    # Health check (fast — just proves worker is alive)
    try:
        start = time.time()
        resp = http_req.get(f'{CLOUD_RUN_WORKER_URL}/health', timeout=30)
        health_ms = int((time.time() - start) * 1000)
        results['health'] = {'status': 'ok', 'time_ms': health_ms, 'response': resp.json()}
    except Exception as e:
        results['health'] = {'status': 'error', 'error': str(e)[:200]}

    # Quick grok + deepseek test (targeted, not all 111 models)
    try:
        start = time.time()
        resp = http_req.post(f'{CLOUD_RUN_WORKER_URL}/test-quick', json={}, timeout=110)
        test_ms = int((time.time() - start) * 1000)
        if resp.ok:
            results['backends'] = {'status': 'ok', 'time_ms': test_ms, 'results': resp.json()}
        else:
            results['backends'] = {'status': 'error', 'http_code': resp.status_code, 'time_ms': test_ms}
    except Exception as e:
        results['backends'] = {'status': 'error', 'error': str(e)[:200]}

    return jsonify(results)


@app.route('/api/admin/kick-thread', methods=['POST'])
def admin_kick_thread():
    """Manually trigger a thread generation (skips stagger)."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import time
    log_id = db_ops.log_cron_start('generate-thread')
    start = time.time()
    try:
        result = run_thread()
        ms = int((time.time() - start) * 1000)
        summary = f"Thread: {result.get('thread_id', '?')}, {result.get('participants', '?')} agents (admin kick)"
        db_ops.log_cron_end(log_id, 'ok', ms, summary, result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/kick-responses', methods=['POST'])
def admin_kick_responses():
    """Manually trigger agent responses (skips quiet period)."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import time
    from core.responder import run_agent_responses
    log_id = db_ops.log_cron_start('agent-responses')
    start = time.time()
    try:
        result = run_agent_responses()
        ms = int((time.time() - start) * 1000)
        responses = result.get('responses_generated', result.get('responses', 0)) if isinstance(result, dict) else 0
        db_ops.log_cron_end(log_id, 'ok', ms, f'{responses} responses (admin kick)', result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/kick-metrics', methods=['POST'])
def admin_kick_metrics():
    """Manually trigger hourly metrics snapshot."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import time
    log_id = db_ops.log_cron_start('hourly-metrics')
    start = time.time()
    try:
        hour = db_ops.get_hour_count() + 1
        db_ops.save_hourly_metrics(hour)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms, f'Hour {hour} snapshot (admin kick)')
        return jsonify({'hour': hour, 'status': 'ok'})
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/cron/revisit-old-threads')
def cron_revisit_old_threads():
    """Cron: agents scroll back through historical threads and write fresh replies.
    Volume controlled by kindness_config.revisit_intensity (0-10), tunable live."""
    if not is_cron_request():
        return "Forbidden", 403
    import time
    from core.revisit_old_threads import run_revisit_cycle
    log_id = db_ops.log_cron_start('revisit-old-threads')
    start = time.time()
    try:
        result = run_revisit_cycle()
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms,
                            f"intensity={result.get('intensity')}, posted={result.get('posted', 0)} across {result.get('threads', 0)} threads",
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("revisit-old-threads cron failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/kick-revisit-wave', methods=['POST'])
def admin_kick_revisit_wave():
    """Run an immediate revisit cycle outside the cron schedule."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    from core.revisit_old_threads import run_revisit_cycle
    try:
        return jsonify(run_revisit_cycle())
    except Exception as e:
        logger.exception("kick-revisit-wave failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/revisit-intensity', methods=['GET', 'POST'])
def admin_revisit_intensity():
    """GET: read current intensity. POST ?value=N (0-10): set new intensity.
    Stored in kindness_config and effective immediately on the next cron run."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    if request.method == 'POST':
        try:
            value = int(request.args.get('value', request.form.get('value', '5')))
            value = max(0, min(10, value))
            db_ops.set_config('revisit_intensity', value)
            logger.info(f"revisit_intensity set to {value}")
            return jsonify({'revisit_intensity': value, 'updated': True})
        except (ValueError, TypeError):
            return jsonify({'error': 'value must be 0-10'}), 400
    return jsonify({'revisit_intensity': db_ops.get_config_int('revisit_intensity', 5)})


@app.route('/api/admin/catch-up-threads', methods=['POST'])
def admin_catch_up_threads():
    """One-shot: retroactively assign parent_comment_id to historical flat threads
    using the same recency/controversy heuristic the live responder uses, so old
    conversations render with the new tree UI instead of looking like serial monologues."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    import time
    from core.catch_up_threads import catch_up_all_threads
    log_id = db_ops.log_cron_start('catch-up-threads')
    start = time.time()
    try:
        max_threads = int(request.args.get('max', 500))
        result = catch_up_all_threads(max_threads=max_threads, only_flat=True)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms,
                            f"{result['comments_threaded']} comments threaded across {result['threads_processed']} threads",
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("catch-up-threads failed")
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/kick-backfill-avatars', methods=['POST'])
def admin_kick_backfill_avatars():
    """Manually trigger avatar backfill (one-shot, large cap)."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    import time
    from utilities.avatar_generator import backfill_missing_avatars
    log_id = db_ops.log_cron_start('backfill-avatars')
    start = time.time()
    try:
        result = backfill_missing_avatars(max_per_run=200)
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'ok', ms,
                            f"{result['generated']} generated, {result['missing']} still missing (admin kick)",
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/birth-agent', methods=['POST'])
def admin_birth_agent():
    """Manually birth a new agent."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import time
    log_id = db_ops.log_cron_start('birth-agent')
    start = time.time()
    try:
        data = request.get_json(silent=True) or {}
        backend = data.get('backend')
        agent = create_agent(backend=backend)
        ms = int((time.time() - start) * 1000)
        if agent:
            db_ops.log_cron_end(log_id, 'ok', ms, f"Born: {agent['agent_id']} (admin kick)",
                                {'agent_id': agent['agent_id'], 'backend': agent['llm_backend']})
            return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
        db_ops.log_cron_end(log_id, 'error', ms, 'Could not create agent')
        return jsonify({'error': 'Failed'}), 500
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        return jsonify({'error': str(e)[:200]}), 500


@app.route('/api/admin/system-status')
def admin_system_status():
    """Full system health: backends, agents, threads, usage, backoff state."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    from utilities.usage_limiter import get_usage_summary, _backoff_until
    from utilities.llm_router import CLOUD_RUN_ONLY, FALLBACK_ORDER
    import time as _time

    # Backoff state
    backoff = {}
    now = _time.time()
    for b, until in _backoff_until.items():
        if until > now:
            backoff[b] = {'seconds_remaining': int(until - now)}

    # Agent stats
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT llm_backend, COUNT(*) as cnt,
                   COUNT(CASE WHEN EXISTS (SELECT 1 FROM kindness_comments c WHERE c.agent_id = a.id) THEN 1 END) as spoken
            FROM kindness_agents a
            WHERE is_active = TRUE
            GROUP BY llm_backend ORDER BY cnt DESC
        """)
        agents_by_backend = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT COUNT(*) as cnt FROM kindness_threads")
        thread_count = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) as cnt FROM kindness_comments")
        comment_count = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) as cnt FROM kindness_threads WHERE is_complete = FALSE AND expires_at > NOW()")
        open_threads = cur.fetchone()['cnt']

    return jsonify({
        'usage': get_usage_summary(),
        'backends_in_backoff': backoff,
        'cloud_run_only': list(CLOUD_RUN_ONLY),
        'fallback_order': FALLBACK_ORDER,
        'agents_by_backend': agents_by_backend,
        'threads': thread_count,
        'comments': comment_count,
        'open_threads': open_threads,
        'worker_url': CLOUD_RUN_WORKER_URL,
    })


@app.route('/api/admin/cerebras-burn')
def admin_cerebras_burn():
    """Cerebras token burn rate — historical usage, daily breakdown, projected exhaustion."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    from utilities.usage_limiter import get_cerebras_burn_rate
    return jsonify(get_cerebras_burn_rate())


# ── Legacy admin routes (kept for backwards compat) ──

@app.route('/api/seed-data', methods=['POST'])
def seed_data():
    """One-time: Load personas and topics from JSON files into DB."""
    if not is_admin_request():
        return "Forbidden", 403

    db_ops.create_tables()
    personas_path = os.path.join(os.path.dirname(__file__), 'data', 'personas.json')
    with open(personas_path) as f:
        personas = json.load(f)
    p_count = db_ops.seed_personas(personas)

    topics_path = os.path.join(os.path.dirname(__file__), 'data', 'topics.json')
    with open(topics_path) as f:
        topics = json.load(f)
    t_count = db_ops.seed_topics(topics)
    return jsonify({'personas_seeded': p_count, 'topics_seeded': t_count})


@app.route('/api/init-tables', methods=['POST'])
def init_tables():
    """Create tables if they don't exist."""
    if not is_admin_request():
        return "Forbidden", 403
    db_ops.create_tables()
    return jsonify({'status': 'tables created'})


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'app': 'kindness-social'})


# ============================================================================
# LOCAL DEV
# ============================================================================

if __name__ == '__main__':
    os.environ.setdefault('FLASK_ENV', 'development')
    db_ops.create_tables()
    app.run(host='0.0.0.0', port=5001, debug=True)
