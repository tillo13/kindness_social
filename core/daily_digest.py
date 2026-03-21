"""Daily Digest — Generates and sends an HTML email summarizing the last 24 hours."""

import logging
from datetime import datetime, timezone

from core.db_ops import get_24h_summary, get_control_vs_treatment, get_featured_thread
from utilities.gmail_utils import send_email

logger = logging.getLogger(__name__)

RECIPIENT = 'andy.tillo@gmail.com'
SITE_URL = 'https://kindness-io.wl.r.appspot.com'


def generate_digest_html(summary, experiment, featured):
    """Build the daily digest email as HTML."""
    now = datetime.now(timezone.utc).strftime('%B %d, %Y')

    # Treatment vs control deltas
    t = experiment.get('treatment', {})
    c = experiment.get('control', {})
    tox_change_t = float(t.get('avg_tox_change', 0) or 0)
    tox_change_c = float(c.get('avg_tox_change', 0) or 0)
    emp_change_t = float(t.get('avg_emp_change', 0) or 0)
    emp_change_c = float(c.get('avg_emp_change', 0) or 0)

    comments = summary.get('comments_24h', 0)
    threads = summary.get('threads_24h', 0)
    improved = summary.get('agents_improved_24h', 0)
    kindness = float(summary.get('avg_kindness_24h', 0) or 0)
    dopamine = int(summary.get('dopamine_24h', 0) or 0)
    bridges = summary.get('bridges_24h', 0)

    # Featured thread section
    featured_html = ''
    if featured and float(featured.get('tox_swing', 0) or 0) > 0:
        featured_html = f"""
        <div style="background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
            <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 8px;">Featured Thread — Biggest Improvement</div>
            <a href="{SITE_URL}/thread/{featured['thread_id']}" style="color: #a8d8ea; text-decoration: none; font-size: 14px;">
                {featured['post_text'][:120]}{'...' if len(featured.get('post_text', '')) > 120 else ''}
            </a>
            <div style="margin-top: 8px; font-size: 12px; color: #888;">
                Toxicity: {int(featured['first_toxicity'])}/10 → {int(featured['last_toxicity'])}/10 (↓{int(featured['tox_swing'])}) · {featured['participant_count']} agents
            </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background: #0d0d1a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<div style="max-width: 560px; margin: 0 auto; padding: 24px 16px;">

    <!-- Header -->
    <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 20px; font-weight: bold; color: #4ade80; margin-bottom: 4px;">Kindness Social</div>
        <div style="font-size: 12px; color: #888;">Daily Digest — {now}</div>
    </div>

    <!-- 24h Stats -->
    <div style="background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
        <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 12px;">Last 24 Hours</div>
        <table style="width: 100%; text-align: center;">
            <tr>
                <td style="padding: 4px;">
                    <div style="font-size: 22px; font-weight: bold; color: #e0e0e0;">{comments}</div>
                    <div style="font-size: 10px; color: #888;">comments</div>
                </td>
                <td style="padding: 4px;">
                    <div style="font-size: 22px; font-weight: bold; color: #e0e0e0;">{threads}</div>
                    <div style="font-size: 10px; color: #888;">threads</div>
                </td>
                <td style="padding: 4px;">
                    <div style="font-size: 22px; font-weight: bold; color: #4ade80;">{improved}</div>
                    <div style="font-size: 10px; color: #888;">improved</div>
                </td>
                <td style="padding: 4px;">
                    <div style="font-size: 22px; font-weight: bold; color: #4ade80;">{kindness:.1f}</div>
                    <div style="font-size: 10px; color: #888;">kindness</div>
                </td>
                <td style="padding: 4px;">
                    <div style="font-size: 22px; font-weight: bold; color: #38bdf8;">{bridges}</div>
                    <div style="font-size: 10px; color: #888;">bridges</div>
                </td>
            </tr>
        </table>
    </div>

    <!-- Experiment -->
    <div style="background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
        <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 12px;">Do Incentives Work?</div>
        <table style="width: 100%; text-align: center;">
            <tr>
                <td style="padding: 8px;">
                    <div style="font-size: 10px; color: #888; margin-bottom: 4px;">Toxicity ↓</div>
                    <div style="font-size: 18px; font-weight: bold; color: #4ade80;">-{tox_change_t:.2f}</div>
                    <div style="font-size: 9px; color: #4ade80;">rewarded</div>
                </td>
                <td style="padding: 8px;">
                    <div style="font-size: 10px; color: #888; margin-bottom: 4px;">Toxicity ↓</div>
                    <div style="font-size: 18px; font-weight: bold; color: #666;">-{tox_change_c:.2f}</div>
                    <div style="font-size: 9px; color: #888;">control</div>
                </td>
                <td style="padding: 8px;">
                    <div style="font-size: 10px; color: #888; margin-bottom: 4px;">Empathy ↑</div>
                    <div style="font-size: 18px; font-weight: bold; color: #4ade80;">+{emp_change_t:.2f}</div>
                    <div style="font-size: 9px; color: #4ade80;">rewarded</div>
                </td>
                <td style="padding: 8px;">
                    <div style="font-size: 10px; color: #888; margin-bottom: 4px;">Empathy ↑</div>
                    <div style="font-size: 18px; font-weight: bold; color: #666;">+{emp_change_c:.2f}</div>
                    <div style="font-size: 9px; color: #888;">control</div>
                </td>
            </tr>
        </table>
        <div style="text-align: center; margin-top: 8px; font-size: 11px; color: #888;">
            {int(t.get('agent_count', 0))} rewarded vs {int(c.get('agent_count', 0))} control agents
        </div>
    </div>

    {featured_html}

    <!-- CTA -->
    <div style="text-align: center; margin: 24px 0;">
        <a href="{SITE_URL}/dashboard" style="display: inline-block; background: #4ade80; color: #0d0d1a; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px;">
            See Full Results
        </a>
    </div>

    <!-- Footer -->
    <div style="text-align: center; font-size: 11px; color: #555; margin-top: 24px; padding-top: 16px; border-top: 1px solid #2a2a4a;">
        "What if social media rewarded kindness?"<br>
        <a href="{SITE_URL}" style="color: #4ade80; text-decoration: none;">kindness-io.wl.r.appspot.com</a>
    </div>

</div>
</body>
</html>"""


def send_daily_digest():
    """Gather data and send the daily digest email."""
    summary = get_24h_summary()
    experiment = get_control_vs_treatment()
    featured = get_featured_thread()

    if not summary or not summary.get('comments_24h'):
        logger.info("No activity in last 24h, skipping digest")
        return {'sent': False, 'reason': 'no activity'}

    html = generate_digest_html(summary, experiment, featured)
    subject = f"Kindness Social — {summary['comments_24h']} comments, {summary['agents_improved_24h']} agents improved"

    success = send_email(subject, html, RECIPIENT)
    return {'sent': success, 'to': RECIPIENT, 'comments_24h': summary['comments_24h']}
