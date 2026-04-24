"""
Kindness Social PostgreSQL Utilities
Connection pooling for the shared kumori Cloud SQL instance.
All tables use the kindness_ prefix.
"""

import logging
import os
import threading
import psycopg2
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
        'user': get_secret('KUMORI_POSTGRES_USERNAME'),
        'password': get_secret('KUMORI_POSTGRES_PASSWORD'),
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

        # Budget: shared db-f1-micro, keep connections low
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=3,
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
                self._pool.putconn(self._conn)
            except Exception:
                pass
            self._conn = None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        self.close()
        return False


def get_db_connection():
    pool = _get_connection_pool()
    conn = pool.getconn()
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
    return PooledConnection(conn, pool)


@contextmanager
def db_cursor(dict_cursor=False):
    """Context manager for DB operations with auto-commit/rollback."""
    conn = get_db_connection()
    cursor_factory = psycopg2.extras.DictCursor if dict_cursor else None
    cursor = conn.cursor(cursor_factory=cursor_factory)
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


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
