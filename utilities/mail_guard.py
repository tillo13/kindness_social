"""The shared-sender guard: one admission check in front of every app's mail (task #34).

Every one of Andy's apps sends as the one consumer Gmail account kumoridotai@gmail.com, so
they share ONE sender reputation. A crash loop mailing an alert per failure got the account
550 5.7.1 / 5.7.30 bounces for all of them (2026-09-06..08). admit() enforces the rule in
code: a repeat of the same recipient + subject inside DEDUPE_MINUTES, or anything past
HOURLY_CAP / DAILY_CAP for the calling app, is refused and recorded, never queued. Subjects
are compared with digits collapsed, so "3 errors" and "4 errors" are one signature.

CANONICAL LOCATION: kumori/utilities/mail_guard.py. Other apps vendor it (deploy.json
shared_files -> utilities/mail_guard.py) and call it first in their own sender, whatever
that sender uses (Gmail API or SMTP):

    from utilities.mail_guard import admit
    ok, reason = admit('myapp', to, subject)
    if not ok:
        return False          # refused: logged here, never retried or queued

The ledger is kumori_ops.mail_ledger on the shared instance, written through the host app's
own utilities.postgres_utils.db_cursor (each app role needs SELECT/INSERT on it). Only kumori
creates the table. If the ledger is unreachable the same caps apply per process instead.
Measured 2026-09-14..21: kumori sends 3-6 a day, never more than 2 in an hour, so the caps sit
~5x over real peak.
"""
import hashlib
import logging
import re
import time
from collections import deque

logger = logging.getLogger(__name__)

DEDUPE_MINUTES = 60
HOURLY_CAP = 10
DAILY_CAP = 40

_local_sent = {}        # app -> deque of (ts, sig); fallback when the ledger is unreachable
_ledger_ensured = False


def _signature(app, to, subject):
    norm = re.sub(r'\d+', '#', (subject or '').strip().lower())
    return hashlib.sha256(f"{app}|{(to or '').strip().lower()}|{norm}".encode()).hexdigest()


def _ensure_ledger(cur):
    """Kumori only: the other app roles cannot CREATE, and a failed DDL aborts the transaction."""
    global _ledger_ensured
    if _ledger_ensured:
        return
    cur.execute("CREATE SCHEMA IF NOT EXISTS kumori_ops")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kumori_ops.mail_ledger (
            id         bigserial PRIMARY KEY,
            app        text NOT NULL,
            to_addr    text NOT NULL,
            sig        char(64) NOT NULL,
            subject    text NOT NULL,
            outcome    text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_mail_ledger_app_time ON kumori_ops.mail_ledger (app, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_mail_ledger_sig_time ON kumori_ops.mail_ledger (sig, created_at)")
    _ledger_ensured = True


def _values(row):
    """Row as a plain list, whether the host's cursor returns tuples or dicts. Every column needs
    its own alias: three bare count(*) columns all arrive keyed 'count' and a dict keeps one."""
    return list(row.values()) if isinstance(row, dict) else list(row)


def _admit_with(cur, app, to, subject):
    """Decide and record in one transaction. Returns (ok, reason)."""
    if app == 'kumori':
        _ensure_ledger(cur)
    sig = _signature(app, to, subject)
    cur.execute("""
        SELECT count(*) FILTER (WHERE sig = %s AND created_at > now() - make_interval(mins => %s)) AS dup,
               count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS hour,
               count(*) FILTER (WHERE created_at > now() - interval '1 day') AS day
        FROM kumori_ops.mail_ledger
        WHERE app = %s AND outcome = 'sent' AND created_at > now() - interval '1 day'
    """, (sig, DEDUPE_MINUTES, app))
    dup, hour, day = _values(cur.fetchone())
    reason = ('duplicate' if dup else 'hourly_cap' if hour >= HOURLY_CAP
              else 'daily_cap' if day >= DAILY_CAP else None)
    cur.execute("INSERT INTO kumori_ops.mail_ledger (app, to_addr, sig, subject, outcome) VALUES (%s, %s, %s, %s, %s)",
                (app, to, sig, (subject or '')[:300], 'sent' if reason is None else f'suppressed_{reason}'))
    return reason is None, reason


def _admit_local(app, to, subject):
    now, sig = time.time(), _signature(app, to, subject)
    q = _local_sent.setdefault(app, deque())
    while q and q[0][0] < now - 86400:
        q.popleft()
    if any(s == sig and t > now - DEDUPE_MINUTES * 60 for t, s in q):
        return False, 'duplicate'
    if sum(1 for t, _ in q if t > now - 3600) >= HOURLY_CAP:
        return False, 'hourly_cap'
    if len(q) >= DAILY_CAP:
        return False, 'daily_cap'
    q.append((now, sig))
    return True, None


def admit(app, to, subject, db_cursor=None):
    """(ok, reason). Call before every send; on refusal, drop the message (it is logged here).
    db_cursor: the host's own context-manager cursor factory taking commit=; defaults to
    utilities.postgres_utils.db_cursor (an app whose DB module lives elsewhere passes its own)."""
    try:
        if db_cursor is None:
            from utilities.postgres_utils import db_cursor
        with db_cursor(commit=True) as cur:
            ok, reason = _admit_with(cur, app, to, subject)
    except Exception as e:
        logger.warning(f"mail_guard: ledger unavailable ({e}); applying per-process caps")
        ok, reason = _admit_local(app, to, subject)
    if not ok:
        logger.error(f"mail_guard: refused ({reason}) app={app} to={to} subject={subject!r}")
    return ok, reason
