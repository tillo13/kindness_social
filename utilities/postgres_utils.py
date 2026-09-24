"""
Kindness Social PostgreSQL Utilities
Connection pooling for the shared kumori Cloud SQL instance.
All tables use the kindness_ prefix.
"""

import logging
import os
import threading
import psycopg2
import psycopg2.extensions
import psycopg2.extras
from contextlib import contextmanager

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = 'kumori-404602'

# The connection itself is kumori's canonical module, vendored on every deploy (deploy.json
# shared_files -> utilities/kumori_db.py); never edit that copy. kindness's settings live in
# app.yaml: KUMORI_DB_AUTH, DB_ROLE, DB_SECRET_PREFIX, DB_POOL_MAX.
from utilities.kumori_db import get_db_connection as _kumori_db_connection  # noqa: E402


def get_db_connection():
    return _kumori_db_connection(GCP_PROJECT_ID)


# ── Runtime DB-speed instrumentation (tier-1, per db-speed-first) ────────────
# The runtime half of the db-speed gate (the static N+1 linter runs at deploy).
# Mirrors galactica's per-request cursor counter + inroads' slow-query timing:
#   • counter — reset_db_counter() in a before_request hook, get_db_counter() to
#     read (app.py warns when a request exceeds DB_CALL_WARN_THRESHOLD). Catches
#     runtime N+1 the static linter can't: a cursor opened in a helper called in
#     a loop.
#   • slow log — any cursor held >= SLOW_QUERY_MS logs its caller site, surfacing
#     slow single queries + connections held too long on the shared f1-micro pool.
from time import perf_counter as _perf_counter
_db_tls = threading.local()
DB_CALL_WARN_THRESHOLD = 20
SLOW_QUERY_MS = int(os.environ.get('KINDNESS_SLOW_QUERY_MS', '500'))


def reset_db_counter():
    _db_tls.count = 0


def get_db_counter() -> int:
    return getattr(_db_tls, 'count', 0)


def _slow_cursor_site():
    import traceback
    here = os.path.basename(__file__)
    for fr in reversed(traceback.extract_stack()[:-2]):
        base = os.path.basename(fr.filename)
        if base != here and 'contextlib' not in fr.filename:
            return f"{base}:{fr.lineno}"
    return 'unknown'


@contextmanager
def db_cursor(dict_cursor=False, commit=True):
    """Context manager for DB operations with auto-commit/rollback.

    dict_cursor=False yields a plain tuple cursor — required by
    kumori_free_llms.backend_registry_db, which indexes rows positionally.
    Cursor factory is set explicitly so the connection's default cannot
    surprise callers that ask for tuples.
    """
    _db_tls.count = getattr(_db_tls, 'count', 0) + 1
    _t0 = _perf_counter()
    conn = get_db_connection()
    if dict_cursor:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else:
        cursor = conn.cursor(cursor_factory=psycopg2.extensions.cursor)
    try:
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
        _ms = (_perf_counter() - _t0) * 1000
        if _ms >= SLOW_QUERY_MS:
            logger.warning("SLOW DB cursor %.0fms (>=%dms) held by %s", _ms, SLOW_QUERY_MS, _slow_cursor_site())


def log_api_usage(model, usage, feature=None, streaming=False,
                  image_count=0, user_id=None, duration_ms=None):
    """Minimal logger for kumori_api_usage. Fire-and-forget background thread.
    Cost left at 0; admin API cost_report is the ground truth for $ reconciliation."""
    import threading

    def _do_log():
        try:
            def _get(k):
                return getattr(usage, k, None) or (usage.get(k, 0) if isinstance(usage, dict) else 0) or 0
            input_tokens = _get('input_tokens')
            output_tokens = _get('output_tokens')
            cache_creation = _get('cache_creation_input_tokens')
            cache_read = _get('cache_read_input_tokens')

            with db_cursor() as cur:
                cur.execute("""
                    INSERT INTO kumori_api_usage
                    (app_name, feature, model, input_tokens, output_tokens,
                     cache_creation_tokens, cache_read_tokens, image_count,
                     streaming, user_id, duration_ms, estimated_cost_usd)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                """, ('kindness_social', feature, model, input_tokens, output_tokens,
                      cache_creation, cache_read, image_count, streaming, user_id, duration_ms))
        except Exception as e:
            logger.warning(f"log_api_usage failed (non-fatal): {e}")

    threading.Thread(target=_do_log, daemon=True).start()
