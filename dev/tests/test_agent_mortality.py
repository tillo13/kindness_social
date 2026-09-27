"""Agent mortality: exact lane retired (or unknown) for 7 days = death; paused = silence; no
backend = never_assigned; kumori unreachable = nothing marked. Offline: plan() is pure."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core import agent_mortality as m

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def row(i, backend, missing_days=None):
    return {'id': i, 'llm_backend': backend,
            'backend_missing_since': None if missing_days is None else NOW - timedelta(days=missing_days)}


class Mortality(unittest.TestCase):
    STATUS = {'gemini': 'retired', 'cerebras': 'paused', 'mistral': 'active', 'ghost': 'unknown'}

    def test_the_rules(self):
        rows = [row(1, None), row(2, 'gemini'), row(3, 'gemini', 3), row(4, 'gemini', 8),
                row(5, 'cerebras'), row(6, 'cerebras', 9), row(7, 'mistral'), row(8, 'ghost'),
                row(9, 'not-asked')]
        p = m.plan(rows, self.STATUS, NOW)
        self.assertEqual(p['never_assigned'], [1])
        self.assertEqual(p['newly_missing'], [2, 8, 9], 'retired and unknown start the clock')
        self.assertEqual(p['died'], [4], 'only after the 7-day grace')
        self.assertEqual(p['back'], [6], 'a paused lane is not death: the clock clears')

    def test_the_real_client_exports_lane_status(self):
        """Not mocked: the sweep imports lane_status from the vendored client package. A missing
        export made every sweep report kumori unreadable (caught before release, 2026-09-26)."""
        from utilities.kumori_api_client import lane_status
        self.assertTrue(callable(lane_status))

    def test_kumori_unreachable_marks_nothing(self):
        with patch.object(m, 'ensure_schema'), patch.object(m, 'lane_statuses', return_value=None), \
             patch.object(m, 'db_cursor') as dbc:
            dbc.return_value.__enter__.return_value.fetchall.return_value = [dict(row(1, 'gemini', 30), now=NOW)]
            out = m.sweep(dry_run=False)
        self.assertFalse(out['ok'])
        self.assertEqual(dbc.call_count, 1, 'only the read; no update ran')


if __name__ == '__main__':
    unittest.main()
