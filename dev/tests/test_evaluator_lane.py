"""Kindness agents keep their EXACT assigned lane (CLAUDE.md: no exceptions, no substitutions).
chat() never asks kumori to substitute; a 404 (lane retired from the catalog) marks the lane gone,
which feeds agent mortality, while a rate limit or outage is plain silence. Offline."""
import unittest
from unittest.mock import patch

from core import evaluator
from utilities.kumori_api_client import KumoriAPIError


class AgentLane(unittest.TestCase):
    def setUp(self):
        evaluator._dead_pins.clear()

    def test_never_asks_for_a_substitute(self):
        with patch.object(evaluator, '_kf_chat', return_value=('hello', 'groq')) as call:
            self.assertEqual(evaluator.chat('groq', [{'role': 'user', 'content': 'x'}]), ('hello', 'groq'))
        self.assertNotIn('substitute', call.call_args.kwargs)

    def test_a_retired_lane_is_marked_gone(self):
        with patch.object(evaluator, '_kf_chat', side_effect=KumoriAPIError('unknown backend', status_code=404)):
            self.assertEqual(evaluator.chat('openrouter-old', []), (None, 'openrouter-old'))
        self.assertTrue(evaluator.lane_gone('openrouter-old'))

    def test_a_rate_limit_is_silence_not_death(self):
        with patch.object(evaluator, '_kf_chat', side_effect=KumoriAPIError('gated', status_code=503)):
            self.assertEqual(evaluator.chat('groq', []), (None, 'groq'))
        self.assertFalse(evaluator.lane_gone('groq'))


if __name__ == '__main__':
    unittest.main()
