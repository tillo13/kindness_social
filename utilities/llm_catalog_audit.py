"""LLM catalog audit + auto-validation for kindness_social.

Lives in kindness_social (not kumori) because the smoke-test step needs
agent_factory + a real public-thread comment. Single nightly cron handles
the whole loop:

  for each provider with rows in kumori_llm_provider_limits:
    fetch live /v1/models catalog
    for each row that's status='active' AND model_id IS NOT in catalog:
        flip to 'retired', log event
    for each model_id in catalog AND no row in DB:
        INSERT row as 'probationary', log 'discovered' event
        run_smoke_test(new_backend) → on pass: flip to 'active', log 'activated'
                                       on fail: increment smoke_attempts, log 'smoke_test_failed'
                                                if attempts >= 3: flip to 'retired_failed_smoke'

Per the design call: smoke test = direct provider API call (not full agent
spawn) on the first round, agent-spawn on the second round. This keeps the
critical path tight — direct API proves the model talks; agent-spawn proves
the full kindness_social pipeline accepts it. Two-stage so a transient
provider hiccup doesn't delete an agent unnecessarily.

Read kumori shared Postgres credentials via the same get_secret pattern the
rest of kindness_social uses.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from utilities.endpoint_parser import parse as _parse_endpoint, map_status as _map_status

import psycopg2.extras

from utilities.postgres_utils import db_cursor, get_secret

logger = logging.getLogger('kindness_social.llm_catalog_audit')

MAX_SMOKE_ATTEMPTS = 3
MAX_DISCOVERIES_PER_PROVIDER_PER_RUN = 3  # Cap so the cron doesn't timeout running
                                          # 100+ agent-spawn round-trips serially.
                                          # Catches up over a few days.

# Per-provider sane defaults for newly-auto-activated rows. Tier=3 keeps them
# out of workhorse rotation; assign_new_agents=False is the soft-launch lever.
PROVIDER_DEFAULTS = {
    'groq':       {'daily_limit': 1000, 'rpm_spacing_sec': 4.0, 'tier': 3, 'shared_pool': 'groq'},
    'sambanova':  {'daily_limit': 200,  'rpm_spacing_sec': 3.0, 'tier': 3, 'shared_pool': None},
    'cerebras':   {'daily_limit': 500,  'rpm_spacing_sec': 3.0, 'tier': 3, 'shared_pool': None},
    'mistral':    {'daily_limit': 100,  'rpm_spacing_sec': 60.0, 'tier': 3, 'shared_pool': None},
    'cohere':     {'daily_limit': 30,   'rpm_spacing_sec': 3.0, 'tier': 3, 'shared_pool': None},
    'openrouter': {'daily_limit': 50,   'rpm_spacing_sec': 10.0, 'tier': 3, 'shared_pool': 'openrouter'},
    'nvidia':     {'daily_limit': 50,   'rpm_spacing_sec': 5.0, 'tier': 3, 'shared_pool': None},
    'github':     {'daily_limit': 50,   'rpm_spacing_sec': 5.0, 'tier': 3, 'shared_pool': None},
    'gemini':     {'daily_limit': 500,  'rpm_spacing_sec': 5.0, 'tier': 3, 'shared_pool': None},
}


def _probe_openai_compat(url: str):
    def probe(api_key: str) -> set[str]:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {api_key}'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return {m['id'] for m in data.get('data', []) if m.get('id')}
    return probe


def _probe_gemini(api_key: str) -> set[str]:
    url = f'https://generativelanguage.googleapis.com/v1beta/models?key={api_key}'
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return {(m.get('name') or '').removeprefix('models/')
            for m in data.get('models', []) if m.get('name')}


PROBES = [
    ('groq',       'KINDNESS_GROQ_API_KEY',       _probe_openai_compat('https://api.groq.com/openai/v1/models'),         'https://api.groq.com/openai/v1/chat/completions'),
    ('sambanova',  'KINDNESS_SAMBANOVA_API_KEY',  _probe_openai_compat('https://api.sambanova.ai/v1/models'),            'https://api.sambanova.ai/v1/chat/completions'),
    ('cerebras',   'KINDNESS_CEREBRAS_API_KEY',   _probe_openai_compat('https://api.cerebras.ai/v1/models'),             'https://api.cerebras.ai/v1/chat/completions'),
    ('mistral',    'KINDNESS_MISTRAL_API_KEY',    _probe_openai_compat('https://api.mistral.ai/v1/models'),              'https://api.mistral.ai/v1/chat/completions'),
    ('cohere',     'KINDNESS_COHERE_API_KEY',     _probe_openai_compat('https://api.cohere.ai/compatibility/v1/models'), 'https://api.cohere.ai/compatibility/v1/chat/completions'),
    ('openrouter', 'KINDNESS_OPENROUTER_API_KEY', _probe_openai_compat('https://openrouter.ai/api/v1/models'),           'https://openrouter.ai/api/v1/chat/completions'),
    ('nvidia',     'KINDNESS_NVIDIA_API_KEY',     _probe_openai_compat('https://integrate.api.nvidia.com/v1/models'),    'https://integrate.api.nvidia.com/v1/chat/completions'),
    ('github',     'SCATTERBRAIN_GITHUB_TOKEN',   _probe_openai_compat('https://models.github.ai/inference/v1/models'),  'https://models.github.ai/inference/v1/chat/completions'),
    ('gemini',     'KINDNESS_GEMINI_API_KEY',     _probe_gemini, None),  # gemini smoke-test handled separately
]
PROBES_BY_PROVIDER = {p[0]: p for p in PROBES}


def _slug(provider: str, model_id: str) -> str:
    """Backend name for a newly-discovered model that fits VARCHAR(50).
    Truncated names get a 4-char hash suffix to prevent PK collisions."""
    import hashlib
    s = model_id.lower().replace('/', '-').replace('_', '-').replace(':', '-').replace('.', '-')
    full = f'{provider}-{s}'
    if len(full) <= 50:
        return full
    h = hashlib.md5(model_id.encode()).hexdigest()[:4]
    return f'{full[:45]}-{h}'


def _smoke_call(backend_name: str, provider: str, model_id: str) -> tuple[bool, str, dict]:
    """REAL smoke test: spawn an agent on this backend and have it comment on
    a random recent public thread. Pass = the entire kindness_social pipeline
    accepted the model. Fail = roll back the agent so the DB is clean.

    Returns (passed, detail, metadata). On pass: detail = "agent X commented on
    thread Y", metadata = {agent_id, thread_id, comment_text_preview}. On fail:
    detail = error message, metadata = {} or partial if agent created.

    This replaces the old direct-API "say hi" probe — that gave false positives
    for models that talk but can't be wired. The agent-spawn round-trip is what
    actually matters: avatar generation, persona injection, comment generation,
    evaluation pipeline, DB save. If any link breaks, the model isn't usable."""
    import random
    agent = None
    try:
        from core.agent_factory import create_agent
        from core.responder import get_open_threads, get_thread_comments, build_reply_context
        from core.evaluator import generate_comment, evaluate_comment
        from core.simulator import calculate_dopamine, update_persona, DEFAULT_CONFIG
        from core import db_ops
        from utilities.postgres_utils import db_cursor as _dc
    except Exception as e:
        return False, f'smoke import failed: {e}', {}

    # 1. Birth the agent. agent_factory rolls back on avatar failure already.
    try:
        agent = create_agent(backend=backend_name)
    except Exception as e:
        return False, f'create_agent raised: {e}', {}
    if not agent:
        return False, 'create_agent returned None (likely avatar generation failure)', {}
    agent_id = agent.get('agent_id', '?')
    agent_db_id = agent['id']

    def _rollback(reason: str):
        try:
            with _dc() as cur:
                cur.execute("DELETE FROM kindness_agents WHERE id = %s", (agent_db_id,))
        except Exception as rb_err:
            logger.warning(f"smoke rollback DELETE failed for {agent_id}: {rb_err}")

    # 2. Pick a random recent public thread
    try:
        open_threads = get_open_threads(limit=20)
    except Exception as e:
        _rollback('get_open_threads failed')
        return False, f'get_open_threads raised: {e}', {'agent_id': agent_id}
    if not open_threads:
        _rollback('no threads available')
        return False, 'no open threads available for smoke test', {'agent_id': agent_id}
    thread = random.choice(open_threads)
    thread_db_id = thread['id']

    # 3. Build context + generate comment via the real pipeline
    try:
        comments = get_thread_comments(thread_db_id) or []
        thread_context = {
            'topic_type': thread.get('topic_type'),
            'keywords':   thread.get('keywords', []),
            'comments':   comments,
            'post_text':  thread.get('post_text', ''),
        }
        target_comment = comments[-1] if comments else None
        reply_context = build_reply_context(thread_context, target_comment)
        thread_history = [{
            'persona': c, 'comment': c.get('comment_text', ''),
            'scores': {
                'kindness': c.get('kindness_score', 5),
                'toxicity': c.get('toxicity_score', 5),
                'empathy':  c.get('empathy_score', 5),
                'bridge':   c.get('bridge_score', 0),
            },
        } for c in reply_context]
        topic = {'post_text': thread['post_text'], 'topic_id': thread.get('topic_id', '?')}
        position = len(comments)

        comment_text, actual_backend, gen_time_ms = generate_comment(
            agent, topic, thread_history, position, DEFAULT_CONFIG
        )
    except Exception as e:
        _rollback('generate_comment raised')
        return False, f'generate_comment failed: {str(e)[:200]}', {'agent_id': agent_id}
    if not comment_text:
        _rollback('generate_comment returned None')
        return False, 'generate_comment returned None (backend unavailable / rate-limited)', {'agent_id': agent_id}

    # 4. Evaluate + score + save (the rest of the pipeline must accept it)
    try:
        scores, eval_time_ms = evaluate_comment(comment_text, agent, thread_history, topic, DEFAULT_CONFIG)
        dopamine, source, multiplier = calculate_dopamine(
            scores, agent, position, thread_history, DEFAULT_CONFIG
        )
        update_persona(agent, scores, dopamine)
        db_ops.update_agent_state(agent['id'], agent)
        parent_id = target_comment['id'] if target_comment and 'id' in target_comment else None
        replied_to = target_comment.get('agent_id') if target_comment else None
        db_ops.save_comment(
            thread_db_id, agent['id'], position, comment_text, scores,
            dopamine, source, multiplier, actual_backend,
            gen_time_ms, eval_time_ms,
            parent_comment_id=parent_id, replied_to_agent_id=replied_to,
        )
    except Exception as e:
        # Comment was generated but post-pipeline failed — keep the agent
        # (it's valid, just missing this comment) but flag the smoke as failed
        # so the cron retries.
        return False, f'post-pipeline failed: {str(e)[:200]}', {
            'agent_id': agent_id, 'thread_id': thread.get('thread_id', '?'),
            'comment_text_preview': comment_text[:120],
        }

    return True, f'agent {agent_id} commented on thread {thread.get("thread_id", "?")}', {
        'agent_id': agent_id,
        'thread_id': thread.get('thread_id', '?'),
        'actual_backend': actual_backend,
        'comment_text_preview': comment_text[:120],
        'kindness': scores.get('kindness'),
        'toxicity': scores.get('toxicity'),
    }


def _log_event(cur, backend, provider, model_id, event_type, reason=None, metadata=None):
    cur.execute("""
        INSERT INTO kumori_llm_registry_events
            (backend, provider, model_id, event_type, reason, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (backend, provider, model_id, event_type, reason,
          json.dumps(metadata) if metadata else None))


# ---------------------------------------------------------------------------
# Dual-write to kumori_llm_endpoints. Catalog audit is the testbed for the
# new normalized schema (post migration 004). Each legacy write site below
# also calls one of these so endpoints stays in parity with provider_limits.
# ---------------------------------------------------------------------------

def _endpoint_set_status(cur, backend: str, new_endpoint_status: str):
    """Update an existing endpoint row's status. Stamps current_status_since
    + retired_at/revived_at as appropriate. No-op if backend isn't in
    endpoints yet (pre-cutover for un-backfilled rows)."""
    try:
        cur.execute("SELECT id, status FROM kumori_llm_endpoints WHERE backend=%s",
                    (backend,))
        r = cur.fetchone()
        if not r:
            return  # endpoint row missing — backfill not yet run for it
        prev_status = r['status'] if isinstance(r, dict) else r[1]
        endpoint_id = r['id'] if isinstance(r, dict) else r[0]
        if prev_status == new_endpoint_status:
            cur.execute("UPDATE kumori_llm_endpoints SET last_active_at=NOW() "
                        "WHERE id=%s", (endpoint_id,))
            return

        # Status flipping
        retired_clause = ', retired_at=NOW()' if new_endpoint_status == 'retired' else ''
        revived_clause = ''
        if prev_status == 'retired' and new_endpoint_status == 'active':
            revived_clause = ', revived_at=NOW()'
            new_endpoint_status = 'revived'
        cur.execute(f"""
            UPDATE kumori_llm_endpoints
               SET status=%s, current_status_since=NOW(),
                   last_active_at=NOW(), updated_at=NOW()
                   {retired_clause}{revived_clause}
             WHERE id=%s
        """, (new_endpoint_status, endpoint_id))

        # Event log
        if new_endpoint_status == 'retired':
            evt = 'endpoint_retired'
        elif new_endpoint_status == 'revived':
            evt = 'endpoint_revived'
        else:
            evt = 'endpoint_status_changed'
        cur.execute("""
            INSERT INTO kumori_llm_registry_events
                (backend, event_type, reason, metadata, endpoint_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (backend, evt, f'{prev_status}->{new_endpoint_status}',
              json.dumps({'from': prev_status, 'to': new_endpoint_status}),
              endpoint_id))
    except Exception as e:
        logger.warning(f"endpoint dual-write skipped for {backend}: {e}")


def _endpoint_upsert_new(cur, backend: str, route: str, model_id_at_route: str,
                         legacy_status: str, daily_limit=None, rpm_spacing_sec=None,
                         notes=None):
    """Insert a newly-discovered endpoint. Parses the model_id to derive
    (model_slug, family). Idempotent ON CONFLICT (backend) DO NOTHING so this
    is safe to call from catalog-audit even if backfill already ran."""
    try:
        parsed = _parse_endpoint(route, model_id_at_route)
        slug = parsed['model_slug']
        family = parsed['family']
        new_status = _map_status(legacy_status)

        # Ensure model exists.
        cur.execute("""
            INSERT INTO kumori_models (slug, family, display_name, modality)
            VALUES (%s, %s, %s, 'chat')
            ON CONFLICT (slug) DO NOTHING
        """, (slug, family, parsed['display_name']))

        # Insert endpoint.
        cur.execute("""
            INSERT INTO kumori_llm_endpoints
                (route, model, model_id_at_route, backend, status, enabled,
                 daily_limit, rpm_spacing_sec, notes)
            VALUES (%s, %s, %s, %s, %s, true, %s, %s, %s)
            ON CONFLICT (backend) DO NOTHING
            RETURNING id
        """, (route, slug, model_id_at_route, backend, new_status,
              daily_limit, rpm_spacing_sec, notes))
        r = cur.fetchone()
        if not r:
            return  # Already existed; ON CONFLICT DO NOTHING.
        endpoint_id = r['id'] if isinstance(r, dict) else r[0]
        cur.execute("""
            INSERT INTO kumori_llm_registry_events
                (backend, provider, model_id, event_type, reason, endpoint_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (backend, route, model_id_at_route, 'endpoint_discovered',
              f'auto-discovered as {new_status}', endpoint_id))
    except Exception as e:
        logger.warning(f"endpoint dual-write (new) skipped for {backend}: {e}")


def run_catalog_audit() -> dict:
    """One end-to-end audit pass. Returns summary dict for the cron route."""
    summary = {
        'newly_retired':      [],
        'newly_activated':    [],
        'smoke_failed':       [],
        'gave_up':            [],
        'confirmed_count':    0,
        'probe_errors':       [],
    }
    now = datetime.now(timezone.utc)

    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT backend, provider, model_id, status, smoke_attempts
            FROM kumori_llm_provider_limits
            WHERE provider IS NOT NULL AND model_id IS NOT NULL
        """)
        db_rows = cur.fetchall()

    db_providers_present = {r['provider'] for r in db_rows}

    # Probe every provider that has any DB row at all (don't dump catalogs
    # of providers we've never wired)
    catalogs = {}
    for provider, secret_name, probe_fn, _ in PROBES:
        if provider not in db_providers_present:
            continue
        try:
            api_key = get_secret(secret_name)
            if not api_key:
                summary['probe_errors'].append({'provider': provider, 'error': f'no secret {secret_name}'})
                continue
            catalogs[provider] = probe_fn(api_key)
        except Exception as e:
            logger.warning(f"catalog audit: {provider} probe failed — {e}")
            summary['probe_errors'].append({'provider': provider, 'error': str(e)[:200]})

    # Diff + write — single transaction per row to limit blast radius
    for row in db_rows:
        provider = row['provider']
        if provider not in catalogs:
            continue  # probe failed, skip this provider entirely this run
        catalog = catalogs[provider]
        in_cat = row['model_id'] in catalog

        if row['status'] == 'active':
            with db_cursor() as cur:
                if in_cat:
                    cur.execute("UPDATE kumori_llm_provider_limits SET last_seen_at=%s WHERE backend=%s",
                                (now, row['backend']))
                    _endpoint_set_status(cur, row['backend'], 'active')
                    summary['confirmed_count'] += 1
                else:
                    reason = f'not in {provider} catalog as of {now.date()}'
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='retired', decommissioned_at=%s,
                               decommission_reason=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (now, reason, row['backend']))
                    _log_event(cur, row['backend'], provider, row['model_id'], 'retired', reason)
                    _endpoint_set_status(cur, row['backend'], 'retired')
                    summary['newly_retired'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id']
                    })
        elif row['status'] == 'probationary':
            # Retry the smoke test for a previously-discovered model
            passed, detail, sm = _smoke_call(row['backend'], provider, row['model_id'])
            with db_cursor() as cur:
                attempts = (row['smoke_attempts'] or 0) + 1
                if passed:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='active', assign_new_agents=TRUE,
                               smoke_attempts=%s, last_seen_at=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (attempts, now, row['backend']))
                    _log_event(cur, row['backend'], provider, row['model_id'], 'activated',
                               reason=f'smoke passed on attempt {attempts}: {detail}',
                               metadata=sm)
                    _endpoint_set_status(cur, row['backend'], 'active')
                    summary['newly_activated'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id'],
                        **sm,
                    })
                elif attempts >= MAX_SMOKE_ATTEMPTS:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='retired_failed_smoke', smoke_attempts=%s,
                               decommissioned_at=%s, decommission_reason=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (attempts, now, f'gave up after {attempts} smoke failures: {detail[:120]}',
                          row['backend']))
                    _log_event(cur, row['backend'], provider, row['model_id'], 'retired_failed_smoke',
                               reason=detail[:300])
                    _endpoint_set_status(cur, row['backend'], 'retired')
                    summary['gave_up'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id'],
                        'last_error': detail[:200],
                    })
                else:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET smoke_attempts=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (attempts, row['backend']))
                    _log_event(cur, row['backend'], provider, row['model_id'], 'smoke_test_failed',
                               reason=detail[:300])
                    summary['smoke_failed'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id'],
                        'attempt': attempts, 'error': detail[:200],
                    })

    # Discover NEW models from each catalog. Cap per provider per run so the
    # smoke-test loop (each round-trip ~5-10s) doesn't blow the App Engine
    # request deadline. Catches up over multiple days.
    known_model_ids_per_provider = {}
    for r in db_rows:
        known_model_ids_per_provider.setdefault(r['provider'], set()).add(r['model_id'])

    # Provider-status gate. Look up canonical_status for each provider — only
    # 'active' providers (verified free-tier policy) get auto-activation.
    # Anything 'pending'/'paused'/'retired' OR a provider not in the table
    # at all → new backends inserted with status='pending_review' so the
    # human-in-the-loop catches surprise provider changes (the
    # 200-OpenRouter-paid-SKU mistake from 2026-05-04).
    provider_canonical_status = {}
    try:
        with db_cursor(dict_cursor=False) as cur:
            cur.execute("SELECT name, canonical_status FROM kumori_llm_providers")
            provider_canonical_status = dict(cur.fetchall())
    except Exception as e:
        logger.warning(f"could not load kumori_llm_providers: {e} — defaulting all to 'pending'")

    def _initial_status(prov):
        return 'probationary' if provider_canonical_status.get(prov) == 'active' else 'pending_review'

    for provider, catalog in catalogs.items():
        # OpenRouter free-tier-only filter (paid SKUs 402 forever).
        if provider == 'openrouter':
            catalog = {m for m in catalog if m.endswith(':free')}
        known = known_model_ids_per_provider.get(provider, set())
        defaults = PROVIDER_DEFAULTS.get(provider, {'daily_limit': 50, 'rpm_spacing_sec': 5.0,
                                                     'tier': 3, 'shared_pool': None})
        new_models = sorted(catalog - known)[:MAX_DISCOVERIES_PER_PROVIDER_PER_RUN]
        initial_status = _initial_status(provider)
        for model_id in new_models:
            new_backend = _slug(provider, model_id)
            with db_cursor() as cur:
                try:
                    # Insert as probationary; smoke test below decides activation.
                    # assign_new_agents is set TRUE on activation, not on insert,
                    # because we never want to mint random agents on a model
                    # that hasn't proven the full pipeline works.
                    cur.execute("""
                        INSERT INTO kumori_llm_provider_limits
                            (backend, provider, model_id, display_name, gateway_model,
                             status, smoke_attempts, daily_limit, rpm_spacing_sec, tier,
                             shared_pool, assign_new_agents, last_seen_at, updated_at, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, FALSE,
                                %s, NOW(), %s)
                        ON CONFLICT (backend) DO NOTHING
                    """, (new_backend, provider, model_id,
                          f'{model_id} ({provider})', new_backend,
                          initial_status,
                          defaults['daily_limit'], defaults['rpm_spacing_sec'],
                          defaults['tier'], defaults['shared_pool'], now,
                          f'auto-discovered by catalog audit (provider canonical_status={provider_canonical_status.get(provider, "missing")})'))
                    _log_event(cur, new_backend, provider, model_id, 'discovered',
                               reason=f'auto-inserted as {initial_status}')
                    _endpoint_upsert_new(cur, new_backend, provider, model_id,
                                         initial_status,
                                         daily_limit=defaults['daily_limit'],
                                         rpm_spacing_sec=defaults['rpm_spacing_sec'],
                                         notes=f'auto-discovered (provider canonical_status={provider_canonical_status.get(provider, "missing")})')
                except Exception as e:
                    logger.warning(f"failed to insert discovered {new_backend}: {e}")
                    continue
            # Real smoke test: agent spawn + comment on a recent thread
            passed, detail, sm = _smoke_call(new_backend, provider, model_id)
            with db_cursor() as cur:
                if passed:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='active', assign_new_agents=TRUE,
                               smoke_attempts=1, last_seen_at=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (now, new_backend))
                    _log_event(cur, new_backend, provider, model_id, 'activated',
                               reason=f'smoke passed: {detail}', metadata=sm)
                    _endpoint_set_status(cur, new_backend, 'active')
                    summary['newly_activated'].append({
                        'backend': new_backend, 'provider': provider, 'model_id': model_id,
                        **sm,
                    })
                else:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET smoke_attempts=1, updated_at=NOW()
                         WHERE backend=%s
                    """, (new_backend,))
                    _log_event(cur, new_backend, provider, model_id, 'smoke_test_failed',
                               reason=detail[:300], metadata=sm)
                    summary['smoke_failed'].append({
                        'backend': new_backend, 'provider': provider, 'model_id': model_id,
                        'attempt': 1, 'error': detail[:200],
                    })

    return summary


def render_digest_html(summary: dict) -> str:
    def table(rows, cols):
        if not rows:
            return '<p><em>— none —</em></p>'
        head = ''.join(f'<th>{label}</th>' for _, label in cols)
        body = ''.join(
            '<tr>' + ''.join(f'<td>{r.get(k, "")}</td>' for k, _ in cols) + '</tr>'
            for r in rows
        )
        return (f'<table cellpadding="6" cellspacing="0" '
                f'style="border-collapse:collapse;border:1px solid #ccc;'
                f'font-family:monospace;font-size:12px;">'
                f'<tr style="background:#eee;">{head}</tr>{body}</table>')

    base = [('backend', 'backend'), ('provider', 'provider'), ('model_id', 'model_id')]
    parts = [
        f'<h3 style="color:#080;">🆕 Newly activated ({len(summary["newly_activated"])})</h3>',
        '<p style="color:#666;font-size:12px;">Smoke test passed; status=active. '
        'Apps see them on next 5-min cache refresh.</p>',
        table(summary['newly_activated'], base),

        f'<h3 style="color:#c00;">🔴 Newly retired ({len(summary["newly_retired"])})</h3>',
        '<p style="color:#666;font-size:12px;">No longer in provider catalog; auto-disabled.</p>',
        table(summary['newly_retired'], base),

        f'<h3 style="color:#a80;">⏸ Smoke failed, will retry ({len(summary["smoke_failed"])})</h3>',
        table(summary['smoke_failed'], base + [('attempt', 'attempt'), ('error', 'error')]),

        f'<h3 style="color:#666;">🪦 Gave up ({len(summary["gave_up"])})</h3>',
        '<p style="color:#666;font-size:12px;">Failed smoke test 3 times; status=retired_failed_smoke.</p>',
        table(summary['gave_up'], base + [('last_error', 'last_error')]),

        f'<p style="color:#666;font-size:12px;">'
        f'Confirmed alive: {summary["confirmed_count"]} backends.</p>',
    ]
    if summary['probe_errors']:
        parts.append('<h3 style="color:#a80;">⚠️ Provider probe errors</h3>')
        parts.append(table(summary['probe_errors'], [('provider', 'provider'), ('error', 'error')]))
    return ''.join(parts)
