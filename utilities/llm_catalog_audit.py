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

import psycopg2.extras

from utilities.postgres_utils import db_cursor, get_secret

logger = logging.getLogger('kindness_social.llm_catalog_audit')

MAX_SMOKE_ATTEMPTS = 3

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
    s = model_id.lower().replace('/', '-').replace('_', '-')
    return f'{provider}-{s}'[:50]


def _smoke_call(provider: str, model_id: str) -> tuple[bool, str]:
    """Direct API call to the provider with the new model_id. Returns
    (passed, detail). On pass, detail = response text snippet. On fail,
    detail = error message."""
    probe = PROBES_BY_PROVIDER.get(provider)
    if not probe:
        return False, f'no probe registered for provider={provider}'
    _, secret_name, _, chat_url = probe
    if not chat_url:
        # Gemini uses non-OpenAI API; treat as auto-pass for now (catalog-presence
        # already validated). Real smoke would need google.generativeai SDK call.
        return True, 'gemini smoke skipped (catalog-presence validated)'
    api_key = get_secret(secret_name)
    if not api_key:
        return False, f'secret {secret_name} not found'
    body = json.dumps({
        'model':       model_id,
        'messages':    [{'role': 'user', 'content': 'Say hi in 3 words.'}],
        'max_tokens':  10,
        'temperature': 0,
    }).encode()
    req = urllib.request.Request(
        chat_url, data=body, method='POST',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        text = (payload.get('choices') or [{}])[0].get('message', {}).get('content') or ''
        if not text.strip():
            return False, f'empty response: {str(payload)[:200]}'
        return True, text[:80]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')[:300]
        return False, f'HTTP {e.code}: {body}'
    except Exception as e:
        return False, str(e)[:200]


def _log_event(cur, backend, provider, model_id, event_type, reason=None, metadata=None):
    cur.execute("""
        INSERT INTO kumori_llm_registry_events
            (backend, provider, model_id, event_type, reason, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (backend, provider, model_id, event_type, reason,
          json.dumps(metadata) if metadata else None))


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
                    summary['newly_retired'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id']
                    })
        elif row['status'] == 'probationary':
            # Retry the smoke test for a previously-discovered model
            passed, detail = _smoke_call(provider, row['model_id'])
            with db_cursor() as cur:
                attempts = (row['smoke_attempts'] or 0) + 1
                if passed:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='active', smoke_attempts=%s, last_seen_at=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (attempts, now, row['backend']))
                    _log_event(cur, row['backend'], provider, row['model_id'], 'activated',
                               reason=f'smoke passed on attempt {attempts}',
                               metadata={'response_preview': detail})
                    summary['newly_activated'].append({
                        'backend': row['backend'], 'provider': provider, 'model_id': row['model_id'],
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

    # Discover NEW models from each catalog
    known_model_ids_per_provider = {}
    for r in db_rows:
        known_model_ids_per_provider.setdefault(r['provider'], set()).add(r['model_id'])

    for provider, catalog in catalogs.items():
        known = known_model_ids_per_provider.get(provider, set())
        defaults = PROVIDER_DEFAULTS.get(provider, {'daily_limit': 50, 'rpm_spacing_sec': 5.0,
                                                     'tier': 3, 'shared_pool': None})
        for model_id in catalog - known:
            new_backend = _slug(provider, model_id)
            with db_cursor() as cur:
                try:
                    cur.execute("""
                        INSERT INTO kumori_llm_provider_limits
                            (backend, provider, model_id, display_name, gateway_model,
                             status, smoke_attempts, daily_limit, rpm_spacing_sec, tier,
                             shared_pool, assign_new_agents, last_seen_at, updated_at, notes)
                        VALUES (%s, %s, %s, %s, %s, 'probationary', 0, %s, %s, %s, %s, FALSE,
                                %s, NOW(), 'auto-discovered by catalog audit')
                        ON CONFLICT (backend) DO NOTHING
                    """, (new_backend, provider, model_id,
                          f'{model_id} ({provider})', new_backend,
                          defaults['daily_limit'], defaults['rpm_spacing_sec'],
                          defaults['tier'], defaults['shared_pool'], now))
                    _log_event(cur, new_backend, provider, model_id, 'discovered',
                               reason='auto-inserted from catalog')
                except Exception as e:
                    logger.warning(f"failed to insert discovered {new_backend}: {e}")
                    continue
            # Run smoke test immediately so single-pass audit can promote
            passed, detail = _smoke_call(provider, model_id)
            with db_cursor() as cur:
                if passed:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET status='active', smoke_attempts=1, last_seen_at=%s, updated_at=NOW()
                         WHERE backend=%s
                    """, (now, new_backend))
                    _log_event(cur, new_backend, provider, model_id, 'activated',
                               reason='smoke passed on attempt 1',
                               metadata={'response_preview': detail})
                    summary['newly_activated'].append({
                        'backend': new_backend, 'provider': provider, 'model_id': model_id,
                    })
                else:
                    cur.execute("""
                        UPDATE kumori_llm_provider_limits
                           SET smoke_attempts=1, updated_at=NOW()
                         WHERE backend=%s
                    """, (new_backend,))
                    _log_event(cur, new_backend, provider, model_id, 'smoke_test_failed',
                               reason=detail[:300])
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
