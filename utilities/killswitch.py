"""Central paid-API killswitch — portable across all kumori-family apps.

CANONICAL LOCATION: _infrastructure/killswitch/killswitch.py
Vendored to consumer apps via deploy.json `shared_files` (same pattern as
anthropic_logger). Apps import as `from utilities.killswitch import check_killswitch`.

Every paid API call across every kumori-family app SHOULD go through
`check_killswitch(provider, ...)` before hitting the provider. If MTD spend
for that provider has crossed the configured cap, the row is set
`enabled=false`, an alert email is sent (best-effort), and a `KillswitchTripped`
exception is raised — the caller's API request never goes out.

State lives in the SHARED kumori Postgres:
  - kumori_api_killswitch  — config: provider, monthly_cap_usd, enabled, trip_reason
  - kumori_api_usage       — per-call rows, summed for MTD spend per provider

Anthropic_logger.logged_create() auto-calls check_killswitch('anthropic') so
any app routing through that wrapper gets killswitch enforcement for free.

Built 2026-04-25 in response to the 2026-04-21 Maps Places $1,045 incident.
Made portable 2026-04-27 so kindness_social (and other apps) can use the
same enforcement instead of being uncapped spend paths.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

# IMPORTANT: this module relies on `utilities.postgres_utils.db_cursor`
# being available in the host app. Every kumori-family app exposes it
# (different host/port/secrets per app, but all point to the same shared
# kumori Postgres where the killswitch tables live).
from utilities.postgres_utils import db_cursor

logger = logging.getLogger('killswitch')


class KillswitchTripped(RuntimeError):
    """Raised when a paid-API call is blocked by the killswitch."""
    def __init__(self, provider: str, reason: str):
        super().__init__(f"[killswitch] {provider} blocked: {reason}")
        self.provider = provider
        self.reason = reason


def get_provider_status(provider: str) -> Optional[dict]:
    """Return current killswitch status for a provider, or None if unconfigured.

    Shape: {provider, monthly_cap_usd, enabled, mtd_spent_usd, trip_reason, tripped_at}.
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT monthly_cap_usd, enabled, trip_reason, tripped_at "
            "FROM kumori_api_killswitch WHERE provider = %s",
            (provider,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cap, enabled, trip_reason, tripped_at = row
        cur.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM kumori_api_usage "
            "WHERE provider = %s AND created_at >= date_trunc('month', NOW())",
            (provider,),
        )
        mtd = float(cur.fetchone()[0] or 0)
    return {
        'provider': provider,
        'monthly_cap_usd': float(cap),
        'enabled': bool(enabled),
        'mtd_spent_usd': mtd,
        'trip_reason': trip_reason,
        'tripped_at': tripped_at.isoformat() if tripped_at else None,
    }


def check_killswitch(provider: str, est_cost: float = 0.0) -> None:
    """Raise KillswitchTripped if `provider` is disabled OR MTD + est_cost >= cap.

    Call this BEFORE every paid-API call. `est_cost` is optional; when 0 we
    just check whether current MTD already exceeds the cap. When non-zero we
    pre-trip when the upcoming call would push us over.

    On trip we set enabled=false and send an alert email exactly once (only
    when the row transitions from enabled=true to false).
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT monthly_cap_usd, enabled, trip_reason "
            "FROM kumori_api_killswitch WHERE provider = %s",
            (provider,),
        )
        row = cur.fetchone()
        if not row:
            # No config = no enforcement. Don't break apps before configured.
            return
        cap, enabled, trip_reason = float(row[0]), bool(row[1]), row[2]

        if not enabled:
            raise KillswitchTripped(provider, trip_reason or 'manually disabled')

        cur.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM kumori_api_usage "
            "WHERE provider = %s AND created_at >= date_trunc('month', NOW())",
            (provider,),
        )
        mtd = float(cur.fetchone()[0] or 0)

        if mtd + est_cost >= cap:
            reason = (f"MTD ${mtd:.2f} + est ${est_cost:.4f} >= cap ${cap:.2f} "
                      f"(UTC {datetime.utcnow().isoformat(timespec='seconds')})")
            cur.execute(
                "UPDATE kumori_api_killswitch SET enabled = FALSE, trip_reason = %s, "
                "tripped_at = NOW(), updated_at = NOW() "
                "WHERE provider = %s AND enabled = TRUE",
                (reason, provider),
            )
            just_tripped = cur.rowcount > 0
            if just_tripped:
                _send_trip_alert(provider, mtd, cap, reason)
                logger.error(f"[killswitch] {provider} TRIPPED: {reason}")
            raise KillswitchTripped(provider, reason)


def _send_trip_alert(provider: str, mtd: float, cap: float, reason: str) -> None:
    """Email Andy when a provider just tripped. Best-effort, never raises.

    Tries each app's gmail_utils signature in turn — kumori uses
    send_email(to, subject, body, from_name=...) while kindness_social uses
    send_email(subject, body, to_emails, is_html=True, from_name=...). We
    swallow signature mismatches so the killswitch trip itself always wins.
    """
    subj = f"🚨 KILLSWITCH TRIPPED — {provider} (${mtd:.2f} of ${cap:.2f})"
    body = (
        f"<p>The {provider} killswitch just tripped.</p>"
        f"<p><b>MTD spent:</b> ${mtd:.2f}<br>"
        f"<b>Cap:</b> ${cap:.2f}<br>"
        f"<b>Reason:</b> {reason}</p>"
        f"<p>All future {provider} calls across the kumori family will be "
        f"blocked until you re-enable at kumori's <code>/admin/killswitch</code>.</p>"
        f"<p>If this is a runaway, also check the provider console "
        f"(e.g. console.anthropic.com) and rotate keys if needed.</p>"
    )
    try:
        from utilities.gmail_utils import send_email
        try:
            # kumori signature: send_email(to, subject, html, from_name=...)
            send_email('andy.tillo@gmail.com', subj, body, from_name='Killswitch')
            return
        except TypeError:
            pass
        try:
            # kindness_social signature: send_email(subject, body, to_emails, ...)
            send_email(subj, body, ['andy.tillo@gmail.com'], is_html=True, from_name='Killswitch')
            return
        except TypeError:
            pass
    except Exception as e:
        logger.warning(f"[killswitch] trip alert email failed: {e}")
