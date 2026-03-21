"""
Kindness Social - Flask Web App
A live, always-running AI social experiment where agents chase kindness points.
"""

import json
import logging
import os
import yaml
from flask import Flask, render_template, jsonify, request

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
# PUBLIC PAGES
# ============================================================================

@app.route('/')
def home():
    """Landing page: the thesis, live proof, CTAs."""
    stats = db_ops.get_global_stats()
    model_data = db_ops.get_model_comparison()
    threads = db_ops.get_recent_threads(limit=3)
    return render_template('home.html', stats=stats, model_data=model_data, threads=threads)


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
    """Public roadmap with per-section comment threads."""
    return render_template('roadmap.html')


@app.route('/about')
def about():
    """About page: the thesis, methodology, and why it matters."""
    return render_template('about.html')


@app.route('/cron-log')
def cron_log():
    """Cron execution history — when jobs ran, timing, results."""
    job_filter = request.args.get('job')
    summary = db_ops.get_cron_summary()
    log = db_ops.get_cron_log(limit=200, job_name=job_filter if job_filter else None)
    return render_template('cron_log.html', summary=summary, log=log, current_job=job_filter)


@app.route('/thread/<thread_id>')
def view_thread(thread_id):
    """View a single discussion thread."""
    thread = db_ops.get_thread_with_comments(thread_id)
    if not thread:
        return "Thread not found", 404
    # Get reactions for all comments in this thread
    reactions = db_ops.get_reactions_for_thread(thread['id']) if thread else {}
    # Get recent threads for sidebar navigation
    recent_threads = db_ops.get_recent_threads(limit=20)
    return render_template('thread.html', thread=thread, reactions=reactions, recent_threads=recent_threads)


@app.route('/agents')
def view_agents():
    """View all agents with stats."""
    sort = request.args.get('sort', 'total_dopamine DESC')
    agents = db_ops.get_all_agents(order_by=sort)
    return render_template('agents.html', agents=agents)


@app.route('/agent/<agent_id>')
def view_agent(agent_id):
    """View a single agent's profile and history."""
    agent = db_ops.get_agent(agent_id)
    if not agent:
        return "Agent not found", 404
    activity = db_ops.get_agent_full_activity(agent_id, limit=30)
    evolution = db_ops.get_agent_evolution(agent['id'])
    return render_template('agent.html', agent=agent, activity=activity, evolution=evolution)


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
            db_ops.log_cron_end(log_id, 'ok', ms,
                                f"Born: {agent['agent_id']} ({agent['llm_backend']})",
                                {'agent_id': agent['agent_id'], 'backend': agent['llm_backend']})
            return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
        db_ops.log_cron_end(log_id, 'error', ms, 'Could not create agent')
        return jsonify({'error': 'Could not create agent'}), 500
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron birth-agent failed")
        return jsonify({'error': str(e)[:200]}), 500


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
