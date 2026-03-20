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


def is_cron_request():
    """Verify request is from App Engine cron (or local dev)."""
    if os.environ.get('FLASK_ENV') == 'development':
        return True
    return request.headers.get('X-Appengine-Cron') == 'true'


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
    return render_template('thread.html', thread=thread)


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
    history = db_ops.get_agent_history(agent_id, limit=30)
    return render_template('agent.html', agent=agent, history=history)


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
    """Cron: Generate a new discussion thread every 30 min."""
    if not is_cron_request():
        return "Forbidden", 403

    logger.info("Cron: generating thread...")
    result = run_thread()
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
# ADMIN / SEED
# ============================================================================

@app.route('/api/seed-data', methods=['POST'])
def seed_data():
    """One-time: Load personas and topics from JSON files into DB."""
    if not is_cron_request():
        return "Forbidden", 403

    db_ops.create_tables()

    # Load and seed personas
    personas_path = os.path.join(os.path.dirname(__file__), 'personas.json')
    with open(personas_path) as f:
        personas = json.load(f)
    p_count = db_ops.seed_personas(personas)

    # Load and seed topics
    topics_path = os.path.join(os.path.dirname(__file__), 'topics.json')
    with open(topics_path) as f:
        topics = json.load(f)
    t_count = db_ops.seed_topics(topics)

    return jsonify({'personas_seeded': p_count, 'topics_seeded': t_count})


@app.route('/api/init-tables', methods=['POST'])
def init_tables():
    """Create tables if they don't exist."""
    if not is_cron_request():
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
