"""
Cron + Admin + Character Creator routes — Flask Blueprint.
Split from app.py to stay under 1000 lines per file.
"""

import json
import logging
import os
import threading

from flask import Blueprint, jsonify, request, render_template

from core import db_ops
from core.simulator import run_thread
from core.agent_factory import create_agent
CLOUD_RUN_WORKER_URL = 'https://kindness-worker-243380010344.us-central1.run.app'  # was: from utilities.backend_registry

logger = logging.getLogger(__name__)

bp = Blueprint('cron_admin', __name__)


# ── Auth helpers (imported from app at registration time) ──

_is_cron = None
_is_admin = None


def init_auth(is_cron_fn, is_admin_fn):
    global _is_cron, _is_admin
    _is_cron = is_cron_fn
    _is_admin = is_admin_fn


def is_cron_request():
    return _is_cron()


def is_admin_request():
    return _is_admin()


# ============================================================================
# CRON ENDPOINTS
# ============================================================================

@bp.route('/api/cron/generate-thread')
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


@bp.route('/api/cron/agent-responses')
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


@bp.route('/api/cron/hourly-metrics')
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


@bp.route('/api/cron/snapshot-agents')
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


@bp.route('/api/cron/agent-reflect')
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
        # Match the agent-responses convention: log no-work cycles as 'skipped'
        # so the /admin cron summary doesn't conflate quiet/no-due-agents
        # with real reflect runs.
        status = 'skipped' if result.get('skipped') or result['reflected'] == 0 else 'ok'
        db_ops.log_cron_end(log_id, status, ms,
                            f'{result["reflected"]} reflected, {result["changed"]} changed',
                            result)
        return jsonify(result)
    except Exception as e:
        ms = int((time.time() - start) * 1000)
        db_ops.log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Cron agent-reflect failed")
        return jsonify({'error': str(e)[:200]}), 500


@bp.route('/api/cron/cron-log-janitor')
def cron_log_janitor():
    """Sweep orphaned 'running' rows in kindness_cron_log.

    A row gets stuck in 'running' when a cron worker dies/timeouts before
    log_cron_end fires (App Engine instance recycled mid-run, OOM, etc.).
    The longest healthy run on record is ~20 min, so anything in 'running'
    for more than 1 hour is definitively orphaned. Mark them as 'error'
    with a clear note so /admin's per-job stats stop being skewed by
    perpetually-running ghost rows (2,225 of them as of 2026-05-07).
    """
    if not is_cron_request():
        return "Forbidden", 403

    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            UPDATE kindness_cron_log
               SET status = 'error',
                   error_text = COALESCE(error_text, '') ||
                                'orphaned: no log_cron_end recorded within 1h (instance died mid-run)'
             WHERE status = 'running'
               AND created_at < NOW() - INTERVAL '1 hour'
            RETURNING job_name
        """)
        flipped = cur.fetchall()
    by_job = {}
    for r in flipped:
        by_job[r['job_name']] = by_job.get(r['job_name'], 0) + 1
    return jsonify({'orphaned_flipped': len(flipped), 'by_job': by_job})


@bp.route('/api/cron/scrape-topics')
def cron_scrape_topics():
    """Cron: Scrape trending headlines and convert to discussion topics via Grok."""
    if not is_cron_request():
        return "Forbidden", 403

    from core.topic_scraper import scrape_and_add_topics
    result = scrape_and_add_topics(CLOUD_RUN_WORKER_URL, max_new=5)
    return jsonify(result)


@bp.route('/api/cron/birth-agent')
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


@bp.route('/api/cron/agent-invites')
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


@bp.route('/api/cron/daily-digest')
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


@bp.route('/api/cron/backfill-avatars')
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


@bp.route('/api/cron/revisit-old-threads')
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


# ============================================================================
# CHARACTER CREATOR — Public page for visitors to create custom agents
# ============================================================================

@bp.route('/create')
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


@bp.route('/api/create-agent', methods=['POST'])
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

    import random
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

@bp.route('/admin')
def admin_page():
    """Admin dashboard — test backends, trigger crons, view health.

    Shows live lifecycle status (active/probationary/flaky/paused/etc) +
    modality alongside each backend so admin can spot 'this one's flaky'
    without bouncing to /llm-lifecycle. Pulled from kumori_llm_endpoints
    JOIN kumori_models — the new 7-status world."""
    if not is_admin_request():
        return "Forbidden — pass ?key=YOUR_KEY", 403
    key = request.headers.get('X-Admin-Key') or request.args.get('key', '')
    from utilities.kumori_api_client import llm_registry as _llm_registry; _r = _llm_registry(); BACKENDS = _r.get('backends', []); LITELLM_BACKENDS = _r.get('litellm_backends', [])
    all_backends = [b['name'] for b in BACKENDS + LITELLM_BACKENDS]

    # Fetch lifecycle status for each backend in one query (vs N+1).
    backend_status = {}
    try:
        from utilities.postgres_utils import db_cursor
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT ep.backend, ep.status, ep.consecutive_probe_passes,
                       ep.consecutive_failures, ep.last_validated_at,
                       ep.last_validation_pass, ep.last_real_traffic_at,
                       COALESCE(m.modality, 'chat') AS modality
                  FROM kumori_llm_endpoints ep
                  LEFT JOIN kumori_models m ON m.slug = ep.model
                 WHERE ep.backend = ANY(%s)
            """, (all_backends,))
            for r in cur.fetchall():
                backend_status[r['backend']] = dict(r)
    except Exception as e:
        logger.warning(f"admin_page: lifecycle lookup failed: {e}")

    # Enrich the backend list with status data so the template can render
    # a status pill per row (admin sees "groq=active 18ms · gemini=paused" etc).
    enriched = []
    for name in all_backends:
        s = backend_status.get(name) or {}
        enriched.append({
            'name': name,
            'status': s.get('status'),
            'modality': s.get('modality') or 'chat',
            'passes': s.get('consecutive_probe_passes') or 0,
            'failures': s.get('consecutive_failures') or 0,
            'last_validation_pass': s.get('last_validation_pass'),
            'last_validated_at': s.get('last_validated_at'),
            'last_real_traffic_at': s.get('last_real_traffic_at'),
        })

    return render_template('admin.html', admin_key=key, backends=all_backends,
                           backends_enriched=enriched)


@bp.route('/api/admin/test-backend', methods=['POST'])
def admin_test_backend():
    """Test a single backend with a quick LLM call."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    backend = data.get('backend', 'groq')

    from utilities.kumori_api_client import llm_chat as _kf_chat
    from utilities.kumori_api_client import llm_registry as _llm_registry; CLOUD_RUN_ONLY = set(_llm_registry().get('cloud_run_only', []))
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
        text, actual = _kf_chat(backend, messages, max_tokens=30, temperature=0.1)
        elapsed = int((time.time() - start) * 1000)
        return jsonify({'status': 'ok', 'backend': backend, 'actual_backend': actual,
                        'response': text[:100], 'time_ms': elapsed})
    except Exception as e:
        return jsonify({'status': 'error', 'backend': backend, 'error': str(e)[:200]})


@bp.route('/api/admin/test-all-backends', methods=['POST'])
def admin_test_all_backends():
    """Test every App Engine backend with a quick call."""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403

    from utilities.kumori_api_client import llm_chat as _kf_chat
    from utilities.kumori_api_client import llm_registry as _llm_registry; _r = _llm_registry(); FALLBACK_ORDER = _r.get('fallback_order', []); CLOUD_RUN_ONLY = set(_r.get('cloud_run_only', []))
    from utilities.kumori_api_client import llm_is_backed_off as is_backend_in_backoff
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
            text, actual = _kf_chat(backend, messages, max_tokens=30, temperature=0.1)
            elapsed = int((time.time() - start) * 1000)
            entry.update({'status': 'ok', 'actual_backend': actual,
                         'response': text[:80], 'time_ms': elapsed})
        except Exception as e:
            entry.update({'status': 'error', 'error': str(e)[:150]})

        results.append(entry)

    working = sum(1 for r in results if r['status'] == 'ok')
    return jsonify({'total': len(results), 'working': working, 'results': results})


@bp.route('/api/admin/test-worker', methods=['POST'])
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


@bp.route('/api/admin/kick-thread', methods=['POST'])
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


@bp.route('/api/admin/kick-responses', methods=['POST'])
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


@bp.route('/api/admin/kick-metrics', methods=['POST'])
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


@bp.route('/api/admin/kick-revisit-wave', methods=['POST'])
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


@bp.route('/api/admin/revisit-intensity', methods=['GET', 'POST'])
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


@bp.route('/api/admin/catch-up-threads', methods=['POST'])
def admin_catch_up_threads():
    """One-shot: retroactively assign parent_comment_id to historical flat threads."""
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


@bp.route('/api/admin/kick-backfill-avatars', methods=['POST'])
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


@bp.route('/api/admin/birth-agent', methods=['POST'])
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


@bp.route('/api/system-status')          # public canonical URL
@bp.route('/api/admin/system-status')    # legacy URL kept for old callers
def admin_system_status():
    """Full system health: backends, agents, threads, usage, backoff state.

    Public — pure DB read + in-process router state. No secrets exposed.
    """

    from utilities.kumori_api_client import llm_usage as get_usage_summary, llm_backoff_until as _backoff_until_fn
    from utilities.kumori_api_client import llm_registry as _llm_registry; _r = _llm_registry(); FALLBACK_ORDER = _r.get('fallback_order', []); CLOUD_RUN_ONLY = set(_r.get('cloud_run_only', []))
    import time as _time

    # Backoff state
    backoff = {}
    now = _time.time()
    for b, until in _backoff_until_fn().items():
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


# ── Removed 2026-04-27: /api/admin/cerebras-burn ──
# Was a stale debug endpoint (hardcoded 3M token estimate from when cerebras
# entered conservation mode, never updated, never rendered anywhere). The
# Service Lifecycle dashboard's daily_used / daily_cap columns are the live
# equivalent. Helper get_cerebras_burn_rate() in db_ops_analytics.py kept
# in case we want to chart it later.


# ── Legacy admin routes ──

@bp.route('/api/seed-data', methods=['POST'])
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


@bp.route('/api/init-tables', methods=['POST'])
def init_tables():
    """Create tables if they don't exist."""
    if not is_admin_request():
        return "Forbidden", 403
    db_ops.create_tables()
    return jsonify({'status': 'tables created'})


@bp.route('/llm-lifecycle')          # public canonical URL — intentional open surface
@bp.route('/admin/llm-lifecycle')    # legacy URL kept so old links don't 404
def admin_llm_lifecycle():
    """Read-only dashboard of LLM + image-gen service lifecycle.

    Shows the new endpoint-lifecycle ladder (discovered → probationary →
    active → flaky → paused → retired/revived) backed by kumori_llm_endpoints,
    plus the legacy provider_limits view for backward compat, plus the
    multi-modality lanes (chat / embedding / rerank / ocr / etc), the
    investigation queue (graduation candidates + scout finds), and a
    monthly chart of activations vs retirements.

    Public — pure DB read + cached HTTP probes; no secrets shown, no money
    spent loading the page. kindness_social is the canary in the open;
    showing the world what's free + what just graduated is part of the
    project's openness story (per Andy 2026-05-05).
    """

    from utilities.postgres_utils import db_cursor

    state = {'active': [], 'probationary': [], 'retired': [], 'retired_failed_smoke': []}

    # Chart bucket query is the same regardless of state — cache 5 min
    chart_buckets, recent_events = _lifecycle_events_cached()

    with db_cursor(dict_cursor=True) as cur:
        # Single GROUP BY for agent counts — replaces the previous N+1
        cur.execute("""
            SELECT llm_backend, COUNT(*) AS cnt
              FROM kindness_agents
             WHERE llm_backend IS NOT NULL
             GROUP BY llm_backend
        """)
        agent_counts = {r['llm_backend']: r['cnt'] for r in cur.fetchall()}

        # Per-backend rollup — calls + fail rate from kumori_llm_daily_caps
        # (kindness only), latency from kumori_llm_health_samples (probes).
        # Per-call telemetry was retired in the Apr 12 shared-router refactor.
        cur.execute("""
            SELECT c.backend,
                   SUM(c.call_count) AS calls,
                   CASE WHEN SUM(c.call_count) > 0
                        THEN ROUND(100.0 * (SUM(c.call_count) - COALESCE(SUM(c.fail_count),0))::numeric / SUM(c.call_count), 1)
                        ELSE NULL END AS success_pct,
                   probe.avg_ms
              FROM kumori_llm_daily_caps c
              LEFT JOIN LATERAL (
                  SELECT ROUND(AVG(latency_ms))::INT AS avg_ms
                  FROM kumori_llm_health_samples
                  WHERE backend = c.backend
                    AND status = 'ok'
                    AND checked_at > NOW() - INTERVAL '7 days'
              ) probe ON TRUE
             WHERE c.app_name = 'kindness_social'
             GROUP BY c.backend, probe.avg_ms
        """)
        telemetry = {r['backend']: r for r in cur.fetchall()}

        cur.execute("""
            SELECT pl.backend, pl.provider, pl.model_id, pl.display_name, pl.status,
                   pl.daily_limit, pl.tier, pl.assign_new_agents,
                   pl.last_seen_at, pl.decommissioned_at, pl.decommission_reason,
                   pl.smoke_attempts
              FROM kumori_llm_provider_limits pl
             WHERE pl.status IN ('active', 'probationary', 'retired', 'retired_failed_smoke')
             ORDER BY pl.status, pl.provider, pl.backend
        """)
        for r in cur.fetchall():
            row = dict(r)
            row['agent_count'] = agent_counts.get(row['backend'], 0)
            t = telemetry.get(row['backend'], {})
            row['telemetry_calls'] = t.get('calls', 0)
            row['telemetry_success_pct'] = t.get('success_pct')
            row['telemetry_avg_ms'] = t.get('avg_ms')
            state[row['status']].append(row)

    # Live in-process backoff state from the LLM router — folds the unique
    # signal from /api/admin/system-status. {backend: seconds_remaining}
    backoff_state = {}
    try:
        from utilities.kumori_api_client import llm_backoff_until as _backoff_until_fn
        import time as _time
        _now = _time.time()
        for b, until in _backoff_until_fn().items():
            if until > _now:
                backoff_state[b] = int(until - _now)
    except Exception:
        pass
    for s in state['active']:
        s['backoff_sec'] = backoff_state.get(s['backend'])

    # Image generation services — live-probe each on dashboard load (cached
    # for 5 min via in-process router state). Same lifecycle reality as LLMs:
    # they go down, get rate-limited, get deprecated. We just have many fewer.
    imggen_services = _imggen_health_snapshot()

    # Endpoint-lifecycle ladder data (kumori_llm_endpoints — the new source
    # of truth post 2026-05-05 lifecycle plan). Modality lanes, investigation
    # queue, scout candidates, trajectory — all the data the daily digest
    # has, surfaced live.
    endpoint_lifecycle = _endpoint_lifecycle_snapshot()

    return render_template(
        'llm_lifecycle.html',
        admin_key=request.headers.get('X-Admin-Key') or request.args.get('key', ''),
        state=state,
        chart_buckets=chart_buckets,
        recent_events=recent_events,
        imggen_services=imggen_services,
        endpoint_lifecycle=endpoint_lifecycle,
    )


def _endpoint_lifecycle_snapshot():
    """Pull live lifecycle ladder data from kumori_llm_endpoints + joined
    catalog tables. Cached 60s via module-level dict. Returns a dict with:
      - modality_grid: [{modality, total, active, probationary, ...}]
      - graduation_candidates: probationary endpoints close to active
      - scout_candidates: route_candidate/model_candidate events from last 7d
      - trajectory: per-provider net change over 30d
      - recent_promotions: endpoint_status_changed events from last 7d
    """
    import time
    if (time.time() - _endpoint_lc_cache['when'] < 60
            and _endpoint_lc_cache['data']):
        return _endpoint_lc_cache['data']

    from utilities.postgres_utils import db_cursor
    out = {'modality_grid': [], 'graduation_candidates': [],
           'scout_candidates': [], 'trajectory': [], 'recent_promotions': [],
           'totals': {}}

    EMOJI = {'chat': '🤖', 'embedding': '🔢', 'rerank': '🔍',
             'image-gen': '🎨', 'image-edit': '✏️', 'image-describe': '📷',
             'ocr': '📄', 'transcribe': '🎙', 'tts': '🔊', 'audio-gen': '🎵',
             'audio': '🎵', 'video-gen': '🎬', 'video-describe': '📹',
             'agent': '🤝', 'multimodal': '🧬', 'unknown': '❓'}

    try:
        with db_cursor(dict_cursor=True) as cur:
            # ── Modality × status grid ──────────────────────────────────
            cur.execute("""
                SELECT COALESCE(m.modality, 'chat') AS modality,
                       e.status, COUNT(*) AS n
                  FROM kumori_llm_endpoints e
                  LEFT JOIN kumori_models m ON m.slug = e.model
                 WHERE e.enabled = true
                 GROUP BY COALESCE(m.modality, 'chat'), e.status
            """)
            grid = {}
            totals = {}
            for r in cur.fetchall():
                grid.setdefault(r['modality'], {})[r['status']] = r['n']
                totals[r['modality']] = totals.get(r['modality'], 0) + r['n']
            ordered = ['chat'] + [m for m, _ in sorted(totals.items(),
                                                         key=lambda kv: -kv[1])
                                  if m != 'chat']
            for mod in ordered:
                if mod not in totals:
                    continue
                g = grid.get(mod, {})
                out['modality_grid'].append({
                    'modality': mod, 'emoji': EMOJI.get(mod, '·'),
                    'total': totals[mod],
                    'active': g.get('active', 0),
                    'probationary': g.get('probationary', 0),
                    'discovered': g.get('discovered', 0),
                    'revived': g.get('revived', 0),
                    'flaky': g.get('flaky', 0),
                    'paused': g.get('paused', 0),
                    'retired': g.get('retired', 0),
                })
            out['totals'] = {
                'endpoints': sum(totals.values()),
                'modalities': len(totals),
            }

            # ── Graduation candidates ───────────────────────────────────
            cur.execute("""
                SELECT e.backend, e.route, e.model, e.status,
                       e.consecutive_probe_passes, e.consecutive_failures,
                       COALESCE(m.modality, 'chat') AS modality,
                       e.last_validated_at, e.last_real_traffic_at,
                       e.last_validation_error,
                       EXTRACT(EPOCH FROM (NOW() - e.discovered_at))/86400
                         AS age_days
                  FROM kumori_llm_endpoints e
                  LEFT JOIN kumori_models m ON m.slug = e.model
                 WHERE e.enabled = true
                   AND (
                     (e.status = 'probationary'
                       AND COALESCE(e.consecutive_probe_passes,0) >= 3)
                     OR e.status = 'discovered'
                     OR (e.status = 'active'
                          AND COALESCE(e.consecutive_failures,0) >= 5)
                   )
                 ORDER BY e.consecutive_probe_passes DESC NULLS LAST,
                          e.discovered_at DESC
                 LIMIT 50
            """)
            for r in cur.fetchall():
                row = dict(r)
                row['emoji'] = EMOJI.get(row['modality'], '·')
                cp = row.get('consecutive_probe_passes') or 0
                if row['status'] == 'probationary' and cp >= 3:
                    row['note'] = f"{max(0, 5 - cp)} more pass(es) → active"
                elif row['status'] == 'discovered':
                    row['note'] = "awaiting first canary probe"
                elif (row.get('consecutive_failures') or 0) >= 5:
                    row['note'] = (f"{row['consecutive_failures']} consecutive "
                                    f"fails — close to flaky")
                else:
                    row['note'] = ''
                out['graduation_candidates'].append(row)

            # ── Scout candidates from last 7 days ───────────────────────
            cur.execute("""
                SELECT backend, provider, reason, metadata, occurred_at
                  FROM kumori_llm_registry_events
                 WHERE event_type IN ('route_candidate', 'model_candidate')
                   AND occurred_at > NOW() - INTERVAL '7 days'
                 ORDER BY occurred_at DESC
                 LIMIT 30
            """)
            for r in cur.fetchall():
                md = r['metadata'] or {}
                out['scout_candidates'].append({
                    'backend': r['backend'],
                    'provider': r['provider'],
                    'name': md.get('name', ''),
                    'context_length': md.get('context_length'),
                    'occurred_at': r['occurred_at'],
                })

            # ── Recent promotions/demotions (last 7d) ───────────────────
            cur.execute("""
                SELECT backend, provider, reason, metadata, occurred_at
                  FROM kumori_llm_registry_events
                 WHERE event_type = 'endpoint_status_changed'
                   AND occurred_at > NOW() - INTERVAL '7 days'
                 ORDER BY occurred_at DESC
                 LIMIT 30
            """)
            for r in cur.fetchall():
                md = r['metadata'] or {}
                out['recent_promotions'].append({
                    'backend': r['backend'],
                    'route': r['provider'],
                    'from': md.get('from', ''),
                    'to': md.get('to', ''),
                    'reason': r['reason'],
                    'occurred_at': r['occurred_at'],
                })

            # ── Trajectory (per-route net change over 30d) ──────────────
            cur.execute("""
                SELECT e.route,
                       COUNT(*) FILTER (
                         WHERE e.discovered_at > NOW() - INTERVAL '30 days') AS added_30d,
                       COUNT(*) FILTER (
                         WHERE e.current_status_since > NOW() - INTERVAL '30 days'
                           AND e.status IN ('paused','retired')) AS lost_30d,
                       COUNT(*) AS total_now
                  FROM kumori_llm_endpoints e
                 WHERE e.enabled = true OR e.status IN ('paused','retired')
                 GROUP BY e.route
                HAVING COUNT(*) FILTER (
                         WHERE e.discovered_at > NOW() - INTERVAL '30 days') > 0
                    OR COUNT(*) FILTER (
                         WHERE e.current_status_since > NOW() - INTERVAL '30 days'
                           AND e.status IN ('paused','retired')) > 0
                 ORDER BY (
                   COUNT(*) FILTER (WHERE e.discovered_at > NOW() - INTERVAL '30 days')
                   - COUNT(*) FILTER (WHERE e.current_status_since > NOW() - INTERVAL '30 days'
                                         AND e.status IN ('paused','retired'))
                 ) DESC
            """)
            for r in cur.fetchall():
                added = int(r['added_30d'] or 0)
                lost = int(r['lost_30d'] or 0)
                net = added - lost
                out['trajectory'].append({
                    'route': r['route'],
                    'added_30d': added,
                    'lost_30d': lost,
                    'net': net,
                    'total_now': int(r['total_now']),
                    'arrow': '🟢 ↑' if net > 0 else ('🔴 ↓' if net < 0 else '⚪ →'),
                })
    except Exception as e:
        logger.warning(f"endpoint_lifecycle_snapshot failed: {e}")

    _endpoint_lc_cache.update({'when': time.time(), 'data': out})
    return out


_endpoint_lc_cache = {'when': 0, 'data': None}


# Module-level caches so repeated dashboard loads don't re-query/re-probe
_imggen_health_cache = {'when': 0, 'data': []}
_lifecycle_events_cache = {'when': 0, 'chart_buckets': [], 'recent_events': []}


def _lifecycle_events_cached(ttl_sec: int = 300):
    """Returns (chart_buckets, recent_events) — cached 5 min so the 12-month
    GROUP BY scan and the recent-events query don't re-run on every page load."""
    import time
    if time.time() - _lifecycle_events_cache['when'] < ttl_sec and _lifecycle_events_cache['chart_buckets']:
        return _lifecycle_events_cache['chart_buckets'], _lifecycle_events_cache['recent_events']
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT TO_CHAR(occurred_at, 'YYYY-MM') AS month,
                   event_type,
                   COUNT(*) AS n
              FROM kumori_llm_registry_events
             WHERE occurred_at > NOW() - INTERVAL '12 months'
             GROUP BY 1, 2
             ORDER BY 1
        """)
        bucket_map = {}
        for r in cur.fetchall():
            b = bucket_map.setdefault(r['month'], {'month': r['month'],
                                                    'activated': 0, 'retired': 0, 'failed': 0})
            if r['event_type'] == 'activated':
                b['activated'] += int(r['n'])
            elif r['event_type'] in ('retired', 'retired_failed_smoke'):
                b['retired'] += int(r['n'])
            elif r['event_type'] == 'smoke_test_failed':
                b['failed'] += int(r['n'])
        chart_buckets = sorted(bucket_map.values(), key=lambda x: x['month'])

        cur.execute("""
            SELECT backend, provider, model_id, event_type, occurred_at, reason
              FROM kumori_llm_registry_events
             ORDER BY occurred_at DESC
             LIMIT 30
        """)
        recent_events = [dict(r) for r in cur.fetchall()]
    _lifecycle_events_cache.update({
        'when': time.time(),
        'chart_buckets': chart_buckets,
        'recent_events': recent_events,
    })
    return chart_buckets, recent_events


def _imggen_health_snapshot():
    """Returns an empty list. Imggen health used to read in-process router
    state when kindness vendored kumori_free_imggen directly. Post-migration
    (2026-05-12), kumori owns the imggen router and the canonical health view
    lives at https://kumori.ai/admin/image-backends — open that page for
    real-time CF + HF probe state, per-model neuron history, etc."""
    return []


@bp.route('/api/cron/llm-catalog-audit')
def cron_llm_catalog_audit():
    """Daily audit + auto-validation of every LLM backend in the registry.

    Probes each provider's /v1/models, retires anything missing, discovers +
    smoke-tests new models. Apps using backend_registry_db pick up changes
    on next 5-min cache refresh.

    Manual: append ?force=1 to bypass the cron-header check.
    """
    if not is_cron_request() and request.args.get('force') != '1':
        return "Forbidden", 403

    from utilities.llm_catalog_audit import run_catalog_audit, render_digest_html
    from utilities.gmail_utils import send_email

    summary = run_catalog_audit()
    n_act = len(summary['newly_activated'])
    n_ret = len(summary['newly_retired'])
    n_fail = len(summary['smoke_failed'])
    n_gave_up = len(summary['gave_up'])

    # Only email if there's news worth reading
    if (n_act or n_ret or n_gave_up or summary['probe_errors']):
        html = (
            f"<h2>Kindness Social — LLM Catalog Audit</h2>"
            f"<p>Daily diff between <code>kumori_llm_provider_limits</code> and "
            f"each provider's live <code>/v1/models</code> catalog, plus smoke-test "
            f"results for newly-discovered models.</p>"
            + render_digest_html(summary) +
            f"<hr><p><small>/api/cron/llm-catalog-audit</small></p>"
        )
        subject = (f"Kumori LLM: {n_act} new · {n_ret} retired · {n_fail} smoke-fail"
                   + (f" · {n_gave_up} gave up" if n_gave_up else ''))
        send_email(subject, html, ['andytillo@gmail.com'])

    return jsonify({
        'newly_activated':   [r['backend'] for r in summary['newly_activated']],
        'newly_retired':     [r['backend'] for r in summary['newly_retired']],
        'smoke_failed':      [r['backend'] for r in summary['smoke_failed']],
        'gave_up':           [r['backend'] for r in summary['gave_up']],
        'confirmed_count':   summary['confirmed_count'],
        'probe_errors':      summary['probe_errors'],
    })


# ─── Agent bio embedding (kumori free-LLM embed pool prototype) ──────────────

@bp.route('/admin/agent-similarity')
def admin_agent_similarity():
    """Two surfaces on one page: per-agent neighbors + free-text search."""
    if not is_admin_request():
        return "Forbidden — pass ?key=YOUR_KEY", 403
    key = request.headers.get('X-Admin-Key') or request.args.get('key', '')
    from core import embed_bios
    embed_bios.ensure_schema()
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT agent_id, display_name,
                   (bio_vec IS NOT NULL) AS has_vec
              FROM kindness_agents
             WHERE is_active = TRUE
             ORDER BY agent_id
        """)
        agents = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM kindness_agents WHERE bio_vec IS NULL AND is_active = TRUE")
        missing_count = cur.fetchone()['count']
    return render_template('admin_agent_similarity.html',
                           admin_key=key, agents=agents,
                           missing_count=missing_count)


@bp.route('/api/admin/embed/backfill', methods=['POST'])
def admin_embed_backfill():
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    from core import embed_bios
    body = request.get_json(silent=True) or {}
    limit = request.args.get('limit') or body.get('limit')
    try:
        limit = int(limit) if limit else None
    except (TypeError, ValueError):
        limit = None
    done, failed = embed_bios.backfill_missing(limit=limit)
    return jsonify({'ok': True, 'embedded': done, 'failed': failed})


@bp.route('/api/admin/embed/similar')
def admin_embed_similar():
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    agent_id = request.args.get('agent_id', '').strip()
    if not agent_id:
        return jsonify({'error': 'agent_id required'}), 400
    try:
        k = int(request.args.get('k', '5'))
    except ValueError:
        k = 5
    from core import embed_bios
    results = embed_bios.similar_to(agent_id, k=k)
    return jsonify({'ok': True, 'agent_id': agent_id, 'results': results})


@bp.route('/api/admin/quality-filter')
def admin_quality_filter_summary():
    """Diagnostic: how many backends would be filtered by quality_filter?"""
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    from core import quality_filter
    return jsonify({'ok': True, **quality_filter.summary()})


@bp.route('/api/admin/embed/search')
def admin_embed_search():
    if not is_admin_request():
        return jsonify({'error': 'Forbidden'}), 403
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'q required'}), 400
    try:
        k = int(request.args.get('k', '10'))
    except ValueError:
        k = 10
    from core import embed_bios
    results, backend = embed_bios.search(q, k=k)
    return jsonify({'ok': True, 'query': q, 'backend': backend, 'results': results})
