"""Agent mortality: an agent whose exact lane is gone from kumori dies and leaves the rotation.

CLAUDE.md: an agent IS its assigned backend, no substitutions. So when kumori retires that lane,
the agent can never speak again. Until 2026-09-26 such agents stayed in the rotation, were asked to
reply every tick, and each refusal was logged as a failed comment (most of the 76-91% "empty
comments" in kindness_live_v1). Like a pilgrims soul leaving the map, the agent now dies:
  - kumori reporting its lane 'retired' (or unknown: kumori has no such lane) is noted
    (backend_missing_since). A PAUSED lane is not death: pauses are kumori's own decisions
    (free-tier conservation, bot blocks) and lanes come back; those agents just stay silent;
  - a lane that comes back clears the note;
  - missing for GRACE_DAYS means death: is_active=FALSE, died_at, death_reason; history kept;
  - an agent created with no backend at all (a May 2026 factory bug) is retired as never_assigned.
New agents keep being born by the birth-agent cron. If kumori can't be asked, nothing is marked,
so an outage can never kill agents.
"""
import functools
import logging

from core.db_ops import db_cursor

logger = logging.getLogger(__name__)

GRACE_DAYS = 7


def ensure_once(fn):
    """Schema DDL at most once per process (db-speed-first: the shared database)."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if wrapped._ensured:
            return
        wrapped._ensured = True
        return fn(*args, **kwargs)
    wrapped._ensured = False
    return wrapped


@ensure_once
def ensure_schema():
    with db_cursor(commit=True) as cur:
        cur.execute("ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS backend_missing_since TIMESTAMPTZ")
        cur.execute("ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS died_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE kindness_agents ADD COLUMN IF NOT EXISTS death_reason TEXT")


GONE = {'retired', 'unknown'}


def lane_statuses(backends):
    """{backend: kumori lifecycle status}, or None when kumori can't be asked."""
    try:
        from utilities.kumori_api_client import lane_status
        names = sorted({b for b in backends if b})
        out = {}
        for i in range(0, len(names), 400):
            out.update(lane_status(names[i:i + 400]))
        return out if names else {}
    except Exception as e:
        logger.warning(f"agent mortality: kumori lane status unreadable, marking nothing: {e}")
        return None


def plan(rows, statuses, now, grace_days=GRACE_DAYS):
    """What the sweep would do, from agent rows and {backend: kumori status} (pure, testable).
    Returns {'never_assigned': [ids], 'died': [ids], 'newly_missing': [ids], 'back': [ids]}."""
    out = {'never_assigned': [], 'died': [], 'newly_missing': [], 'back': []}
    for r in rows:
        backend, since = r.get('llm_backend'), r.get('backend_missing_since')
        if not backend:
            out['never_assigned'].append(r['id'])
        elif statuses.get(backend, 'unknown') not in GONE:
            if since:
                out['back'].append(r['id'])
        elif since is None:
            out['newly_missing'].append(r['id'])
        elif (now - since).days >= grace_days:
            out['died'].append(r['id'])
    return out


def sweep(dry_run=True, grace_days=GRACE_DAYS):
    """Run one mortality pass. dry_run reports without writing."""
    ensure_schema()
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""SELECT id, agent_id, llm_backend, backend_missing_since, NOW() AS now
                         FROM kindness_agents WHERE is_active = TRUE""")
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return {'ok': True, 'dry_run': dry_run, 'active': 0}
    statuses = lane_statuses(r['llm_backend'] for r in rows)
    if statuses is None:
        return {'ok': False, 'error': 'kumori lane status unreadable; nothing marked'}
    p = plan(rows, statuses, rows[0]['now'], grace_days)
    by_id = {r['id']: r for r in rows}
    result = {'ok': True, 'dry_run': dry_run, 'active': len(rows),
              **{k: len(v) for k, v in p.items()},
              'died_lanes': sorted({by_id[i]['llm_backend'] for i in p['died']}),
              'missing_lanes': sorted({by_id[i]['llm_backend'] for i in p['newly_missing']})}
    if dry_run:
        return result
    with db_cursor(commit=True) as cur:
        if p['newly_missing']:
            cur.execute("UPDATE kindness_agents SET backend_missing_since = NOW() WHERE id = ANY(%s)",
                        (p['newly_missing'],))
        if p['back']:
            cur.execute("UPDATE kindness_agents SET backend_missing_since = NULL WHERE id = ANY(%s)", (p['back'],))
        if p['died']:
            cur.execute("""UPDATE kindness_agents SET is_active = FALSE, died_at = NOW(),
                                  death_reason = 'lane_gone_' || %s || 'd' WHERE id = ANY(%s)""",
                        (str(grace_days), p['died']))
        if p['never_assigned']:
            cur.execute("""UPDATE kindness_agents SET is_active = FALSE, died_at = NOW(),
                                  death_reason = 'never_assigned' WHERE id = ANY(%s)""", (p['never_assigned'],))
    logger.info(f"agent mortality: {result}")
    return result
