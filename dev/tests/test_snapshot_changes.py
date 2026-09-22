"""Agent snapshots are change-only: a birth row, then a row only when a trait moves.

Measured 2026-09-21: 1,051,822 snapshot rows in 30 days, 135 of them real changes, so
writing every 30 minutes stored identical rows (4.6M rows / 1.4 GB on the shared Cloud SQL).
The evolution chart reads the change rows plus the agent's current values as the last point.

Scratch agent only (agent_id prefix _test_snapchange), removed again. Skipped, not failed,
when Postgres is unreachable.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db_ops_analytics import get_agent_evolution, snapshot_all_agents  # noqa: E402
from utilities.postgres_utils import db_cursor                             # noqa: E402

AGENT = '_test_snapchange_agent'


def _reachable():
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


class ChangeOnlySnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _reachable():
            raise unittest.SkipTest("Postgres not reachable from this environment")

    def setUp(self):
        self._cleanup()
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""INSERT INTO kindness_agents
                             (agent_id, display_name, political_lean, toxicity_baseline, current_toxicity,
                              empathy_baseline, current_empathy, openness_to_change, humor,
                              is_active, total_interactions)
                           VALUES (%s, 'Snapshot Tester', 0, 0.1, 0.1, 0.5, 0.5, 0.5, 5.0, TRUE, 1)
                           RETURNING id""", (AGENT,))
            self.agent = cur.fetchone()['id']

    def tearDown(self):
        self._cleanup()

    @staticmethod
    def _cleanup():
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""DELETE FROM kindness_agent_snapshots WHERE agent_id IN
                           (SELECT id FROM kindness_agents WHERE agent_id LIKE %s)""", (AGENT + '%',))
            cur.execute("DELETE FROM kindness_agents WHERE agent_id LIKE %s", (AGENT + '%',))

    def _snap(self, hour):
        return snapshot_all_agents(hour, agent_ids=[self.agent])

    def test_birth_then_only_changes(self):
        self.assertEqual(self._snap(1), 1, "first snapshot is the birth row")
        self.assertEqual(self._snap(2), 0, "unchanged traits must not write a row")
        self.assertEqual(self._snap(3), 0)
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("UPDATE kindness_agents SET humor = 7.5 WHERE id = %s", (self.agent,))
        self.assertEqual(self._snap(4), 1, "a trait change writes exactly one row")
        self.assertEqual(self._snap(5), 0)

    def test_evolution_is_changes_plus_now(self):
        self._snap(1)
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("UPDATE kindness_agents SET humor = 7.5 WHERE id = %s", (self.agent,))
        self._snap(2)
        evo = get_agent_evolution(self.agent)
        self.assertEqual([round(p['humor'], 1) for p in evo], [5.0, 7.5, 7.5],
                         "birth, the change, then current values as the last point")


if __name__ == '__main__':
    unittest.main()
