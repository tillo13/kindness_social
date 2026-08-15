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
import psycopg2.pool
from contextlib import contextmanager

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = 'kumori-404602'

_credentials_cache = {}
_connection_pools = {}
_pool_lock = threading.Lock()


def get_secret(secret_id, default=None):
    from utilities.google_secret_utils import get_secret as _get_secret
    return _get_secret(secret_id, default)


def get_postgres_credentials():
    global _credentials_cache
    if GCP_PROJECT_ID in _credentials_cache:
        return _credentials_cache[GCP_PROJECT_ID]

    creds = {
        'host': get_secret('KUMORI_POSTGRES_IP'),
        'dbname': get_secret('KUMORI_POSTGRES_DB_NAME'),
        'user': get_secret('KINDNESS_POSTGRES_USERNAME'),
        'password': get_secret('KINDNESS_POSTGRES_PASSWORD'),
        'connection_name': get_secret('KUMORI_POSTGRES_CONNECTION_NAME'),
    }
    _credentials_cache[GCP_PROJECT_ID] = creds
    return creds


def _get_connection_pool():
    global _connection_pools
    with _pool_lock:
        if GCP_PROJECT_ID in _connection_pools:
            return _connection_pools[GCP_PROJECT_ID]

        db_credentials = get_postgres_credentials()
        is_gcp = os.environ.get('GAE_ENV', '').startswith('standard')

        if is_gcp:
            db_socket_dir = os.environ.get("DB_SOCKET_DIR", "/cloudsql")
            host = f"{db_socket_dir}/{db_credentials['connection_name']}"
        else:
            host = db_credentials['host']

        # Budget: shared db-f1-micro (50 conns across all projects). app.yaml
        # pins max_instances=1, so this pool is the ONLY kindness process — its
        # maxconn is the hard ceiling kindness can ever hold. Bumped 3→8
        # (2026-05-31) to give the cron fleet + web traffic headroom on the
        # single F1 instance; the top-of-hour PoolError bursts were a maxconn=3
        # pool starved by colliding crons (now also staggered in cron.yaml).
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=8,
            dbname=db_credentials['dbname'],
            user=db_credentials['user'],
            password=db_credentials['password'],
            host=host,
            connect_timeout=10,
            options='-c statement_timeout=30000'
        )
        _connection_pools[GCP_PROJECT_ID] = pool
        logger.info("Created kindness connection pool")
        return pool


class PooledConnection:
    """Returns connection to pool on close() instead of closing it."""
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool

    def close(self):
        if self._conn:
            try:
                # putconn rolls back open txns but does NOT reset autocommit —
                # without this, a borrower who flipped it would leak an
                # autocommit connection to the next borrower.
                if self._conn.autocommit:
                    self._conn.autocommit = False
                self._pool.putconn(self._conn)
            except Exception:
                pass
            self._conn = None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        # Delegate writes symmetrically with __getattr__ — without this,
        # `conn.autocommit = True` lands on the wrapper and silently no-ops
        # (pilgrims 2026-08: three sessions "applied" DDL that rolled back on putconn).
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        self.close()
        return False


def get_db_connection():
    """Acquire a connection. Retries on PoolError with short backoff —
    psycopg2's ThreadedConnectionPool fails fast (no internal wait) when
    all maxconn slots are busy, but in practice most contention windows
    are sub-second since queries are short. Blocking up to ~2s lets cron
    + web traffic coexist on a tight maxconn=3 pool without spurious
    'connection pool exhausted' errors. Beyond 2s the underlying problem
    is real and deserves to surface.
    """
    import time as _time
    pool = _get_connection_pool()
    conn = None
    deadline = _time.time() + 2.0
    backoff = 0.05
    while True:
        try:
            conn = pool.getconn()
            break
        except psycopg2.pool.PoolError:
            if _time.time() >= deadline:
                raise
            _time.sleep(backoff)
            backoff = min(backoff * 1.7, 0.4)
    # Test if connection is alive — Cloud SQL kills idle connections
    try:
        conn.cursor().execute("SELECT 1")
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        logger.warning("Stale DB connection detected, reconnecting")
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        _connection_pools.pop(GCP_PROJECT_ID, None)
        pool = _get_connection_pool()
        conn = pool.getconn()
    # end the probe's implicit txn — hand the conn out clean, not
    # idle-in-transaction (also lets callers set session flags like
    # autocommit, which raise mid-transaction)
    conn.rollback()
    return PooledConnection(conn, pool)


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
