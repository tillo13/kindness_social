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


CLOUD_RUN_WORKER_URL = 'https://kindness-worker-g7dpldf2xq-uc.a.run.app'

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
def index():
    """Homepage: live dashboard + recent threads."""
    stats = db_ops.get_global_stats()
    threads = db_ops.get_recent_threads(limit=15)
    model_data = db_ops.get_model_comparison()
    metrics = db_ops.get_metrics_history(limit=48)  # Last 48 hours
    return render_template('index.html',
                           stats=stats, threads=threads,
                           model_data=model_data, metrics=metrics)


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
    return render_template('agent.html', agent=agent, activity=activity)


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
    """Cron: Maybe generate a new discussion thread.
    ~60% chance each call — staggered so threads don't arrive like clockwork."""
    if not is_cron_request():
        return "Forbidden", 403

    import random
    if random.random() < 0.4:
        logger.info("Cron: skipping thread generation this round (stagger)")
        return jsonify({'skipped': 'stagger', 'status': 'ok'})

    logger.info("Cron: generating thread...")
    result = run_thread()
    return jsonify(result)


@app.route('/api/cron/agent-responses')
def cron_agent_responses():
    """Cron: Agents check open threads and decide whether to respond."""
    if not is_cron_request():
        return "Forbidden", 403

    from core.responder import run_agent_responses
    logger.info("Cron: running agent responses...")
    result = run_agent_responses()
    return jsonify(result)


@app.route('/api/cron/hourly-metrics')
def cron_hourly_metrics():
    """Cron: Calculate and save hourly aggregate metrics."""
    if not is_cron_request():
        return "Forbidden", 403

    hour = db_ops.get_hour_count() + 1
    db_ops.save_hourly_metrics(hour)
    return jsonify({'hour': hour, 'status': 'ok'})


@app.route('/api/cron/birth-agent')
def cron_birth_agent():
    """Cron: Birth a new agent with a random backend."""
    if not is_cron_request():
        return "Forbidden", 403

    agent = create_agent()
    if agent:
        return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
    return jsonify({'error': 'Could not create agent'}), 500


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

    data = request.get_json() or {}
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
                timeout=60,
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
    """Test Cloud Run worker health and grok/deepseek backends."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    import requests as http_req
    import time

    # Health check
    try:
        start = time.time()
        resp = http_req.get(f'{CLOUD_RUN_WORKER_URL}/health', timeout=10)
        health_ms = int((time.time() - start) * 1000)
        health = {'status': 'ok', 'time_ms': health_ms, 'response': resp.json()}
    except Exception as e:
        health = {'status': 'error', 'error': str(e)[:200]}

    # Test all models via worker
    try:
        start = time.time()
        resp = http_req.post(f'{CLOUD_RUN_WORKER_URL}/test-all-models', json={}, timeout=120)
        test_ms = int((time.time() - start) * 1000)
        if resp.ok:
            models = {'status': 'ok', 'time_ms': test_ms, 'response': resp.json()}
        else:
            models = {'status': 'error', 'http_code': resp.status_code, 'time_ms': test_ms}
    except Exception as e:
        models = {'status': 'error', 'error': str(e)[:200]}

    return jsonify({'health': health, 'models': models})


@app.route('/api/admin/kick-thread', methods=['POST'])
def admin_kick_thread():
    """Manually trigger a thread generation (skips stagger)."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    result = run_thread()
    return jsonify(result)


@app.route('/api/admin/kick-responses', methods=['POST'])
def admin_kick_responses():
    """Manually trigger agent responses (skips quiet period)."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    from core.responder import run_agent_responses
    result = run_agent_responses()
    return jsonify(result)


@app.route('/api/admin/kick-metrics', methods=['POST'])
def admin_kick_metrics():
    """Manually trigger hourly metrics snapshot."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    hour = db_ops.get_hour_count() + 1
    db_ops.save_hourly_metrics(hour)
    return jsonify({'hour': hour, 'status': 'ok'})


@app.route('/api/admin/birth-agent', methods=['POST'])
def admin_birth_agent():
    """Manually birth a new agent."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json() or {}
    backend = data.get('backend')
    agent = create_agent(backend=backend)
    if agent:
        return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
    return jsonify({'error': 'Failed'}), 500


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
    personas_path = os.path.join(os.path.dirname(__file__), 'personas.json')
    with open(personas_path) as f:
        personas = json.load(f)
    p_count = db_ops.seed_personas(personas)

    topics_path = os.path.join(os.path.dirname(__file__), 'topics.json')
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
