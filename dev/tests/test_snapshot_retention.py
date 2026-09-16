"""Old agent snapshots thin to one per day; the recent month stays intact.

kindness_agent_snapshots reached 5.4M rows / 1,222 MB — the second-biggest
table on the shared Cloud SQL instance (2026-09-15), 81% of it older than 30
days, on a disk about 1 GB from Google's paid auto-resize. The evolution chart
reads a per-agent series, so the old part keeps a daily point and nothing in
the last 30 days is touched.

Scratch agent only (agent_id prefix _test_retention), removed again. Skipped,
not failed, when Postgres is unreachable.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db_ops_analytics import prune_agent_snapshots          # noqa: E402
from utilities.postgres_utils import db_cursor                    # noqa: E402

AGENT = '_test_retention_agent'


def _reachable():
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


class PruneAgentSnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _reachable():
            raise unittest.SkipTest("Postgres not reachable from this environment")

    def setUp(self):
        self._cleanup()
        with db_cursor(dict_cursor=True) as cur:
            agents = []
            for suffix in ('', '_second'):
                cur.execute("""INSERT INTO kindness_agents
                                 (agent_id, display_name, political_lean, toxicity_baseline,
                                  current_toxicity, empathy_baseline, current_empathy, openness_to_change)
                               VALUES (%s, 'Retention Tester', 0, 0.1, 0.1, 0.5, 0.5, 5.0)
                               RETURNING id""", (AGENT + suffix,))
                agents.append(cur.fetchone()['id'])
            self.agent, self.agent_two = agents
            # four rows on each of two old days, plus two inside the 30-day window
            for days_ago, hours in ((90, (0, 6, 12, 18)), (60, (0, 6, 12, 18)), (2, (0, 6))):
                for hour in hours:
                    # anchored to midnight, then hours ADDED: NOW() minus both
                    # days and hours lands on the previous calendar day
                    cur.execute("""INSERT INTO kindness_agent_snapshots
                                     (agent_id, hour_number, current_toxicity, created_at)
                                   VALUES (%s, %s, 0.1,
                                           (NOW()::date - make_interval(days => %s))
                                             + make_interval(hours => %s))""",
                                (self.agent, days_ago * 24 + hour, days_ago, hour))
                    cur.execute("""INSERT INTO kindness_agent_snapshots
                                     (agent_id, hour_number, current_toxicity, created_at)
                                   VALUES (%s, %s, 0.1,
                                           (NOW()::date - make_interval(days => %s))
                                             + make_interval(hours => %s))""",
                                (self.agent_two, days_ago * 24 + hour, days_ago, hour))

    def tearDown(self):
        self._cleanup()

    @staticmethod
    def _cleanup():
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""DELETE FROM kindness_agent_snapshots WHERE agent_id IN
                           (SELECT id FROM kindness_agents WHERE agent_id LIKE %s)""", (AGENT + '%',))
            cur.execute("DELETE FROM kindness_agents WHERE agent_id LIKE %s", (AGENT + '%',))

    def _count(self, agent):
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT count(*) AS n FROM kindness_agent_snapshots WHERE agent_id = %s", (agent,))
            return cur.fetchone()['n']

    def _rows(self):
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""SELECT created_at::date AS day, count(*) AS n
                             FROM kindness_agent_snapshots WHERE agent_id = %s
                            GROUP BY 1 ORDER BY 1""", (self.agent,))
            return {str(r['day']): r['n'] for r in cur.fetchall()}

    def test_old_days_keep_one_row_and_recent_days_keep_all(self):
        before = self._rows()
        self.assertEqual(sorted(before.values()), [2, 4, 4])
        self.assertEqual(prune_agent_snapshots(pause_s=0, agent_ids=[self.agent]), 6)
        after = self._rows()
        self.assertEqual(sorted(after.values()), [1, 1, 2], after)
        self.assertEqual(set(before), set(after), 'no day may disappear entirely')

    def test_a_second_pass_deletes_nothing(self):
        prune_agent_snapshots(pause_s=0, agent_ids=[self.agent])
        after = self._rows()
        self.assertEqual(prune_agent_snapshots(pause_s=0, agent_ids=[self.agent]), 0)
        self.assertEqual(self._rows(), after)

    def test_the_window_decides_what_is_old(self):
        """full_detail_days=365 puts every row inside the window: nothing goes."""
        before = self._rows()
        self.assertEqual(prune_agent_snapshots(full_detail_days=365, pause_s=0, agent_ids=[self.agent]), 0)
        self.assertEqual(self._rows(), before)

    def test_the_cap_stops_before_the_next_agent(self):
        """The first runs face a 4.3M-row backlog, which the cron must not try
        in one web request. The cap is checked per agent, so an agent is never
        left half-pruned: this run takes the first one and leaves the second."""
        from core.db_ops_analytics import prune_agent_snapshots as prune
        both = [self.agent, self.agent_two]
        self.assertEqual(prune(pause_s=0, agent_ids=both, max_rows=1), 6)
        self.assertEqual((self._count(self.agent), self._count(self.agent_two)), (4, 10))
        self.assertEqual(prune(pause_s=0, agent_ids=both, max_rows=1), 6)
        self.assertEqual((self._count(self.agent), self._count(self.agent_two)), (4, 4))
        self.assertEqual(prune(pause_s=0, agent_ids=both, max_rows=1), 0)

if __name__ == '__main__':
    unittest.main()
