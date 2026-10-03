"""The digest's incentive box must show each change with its true sign.

2026-10-03: avg_tox_change is baseline - current, and the template hard-coded a '-' in
front of it. Rewarded agents went 4.24 -> 4.84 (more toxic) and printed '--0.61';
control went 4.03 -> 3.94 and printed '-0.10'. Both read as a drop under 'Toxicity ↓'.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.daily_digest import generate_digest_html  # noqa: E402


class DigestSigns(unittest.TestCase):
    def test_changes_print_with_their_true_sign(self):
        experiment = {'treatment': {'avg_tox_change': -0.61, 'avg_emp_change': 0.21, 'agent_count': 811},
                      'control': {'avg_tox_change': 0.10, 'avg_emp_change': -0.05, 'agent_count': 32}}
        html = generate_digest_html({}, experiment, None)
        self.assertIn('+0.61', html)     # rewarded got more toxic
        self.assertIn('-0.10', html)     # control got less toxic
        self.assertIn('+0.21', html)
        self.assertIn('-0.05', html)     # an empathy drop, never '+-0.05'
        for bad in ('--0.61', '+-0.05', '--', '+-'):
            self.assertNotIn(f'>{bad}', html.replace(' ', ''))

    def test_dashboard_and_home_lines_render_true_signs(self):
        """The site had the same bug: rising toxicity lost its sign, a falling empathy kept a '+'."""
        from pathlib import Path
        from jinja2 import Template
        root = Path(__file__).resolve().parents[2] / 'templates'
        t = {'avg_tox_change': -0.61, 'avg_emp_change': -0.05}
        c = {'avg_tox_change': 0.10, 'avg_emp_change': 0.09}
        dash = [l.strip() for l in (root / 'dashboard.html').read_text().splitlines()
                if '_change' in l and '{{' in l and ('avg_tox' in l or 'avg_emp' in l)]
        out = [Template(l).render(t=t, c=c) for l in dash]
        self.assertEqual(out, ['+0.6', '-0.1', '-0.1', '+0.1'])
        home = [l.strip() for l in (root / 'home.html').read_text().splitlines()
                if 'is_negative' in l and '{{' in l]
        rows = [('Toxicity Change', -0.61, 0.10, True), ('Empathy Change', -0.05, 0.09, False)]
        got = [Template(l).render(label=lb, t_val=tv, c_val=cv, is_negative=neg)
               for lb, tv, cv, neg in rows for l in home]
        self.assertEqual(got, ['+0.6', '-0.1', '-0.1', '+0.1'])


if __name__ == '__main__':
    unittest.main()
