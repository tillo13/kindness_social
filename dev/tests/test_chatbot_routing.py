"""Offline site-chat routing checks. No experiment agents or DB are touched."""
import unittest
from unittest.mock import Mock, patch
from core import chatbot
from utilities import kumori_api_client


class ChatbotRouting(unittest.TestCase):
    def setUp(self):
        self.count = self.enterContext(patch.object(chatbot, 'get_chat_count_today', return_value=0))
        self.enterContext(patch.object(chatbot, 'build_experiment_context', return_value='test context'))
        self.logged = self.enterContext(patch.object(chatbot, 'log_chat_message'))
        self.free = self.enterContext(patch.object(kumori_api_client, 'llm_chat_resilient',
                                                  return_value=('Free answer.', 'free', [], None)))
        self.paid = self.enterContext(patch.object(kumori_api_client, 'llm_chat_reserve',
                                                  side_effect=AssertionError('No paid fallback')))

    def test_free_response_and_context(self):
        history = [{'role': 'user', 'content': str(n)} for n in range(30)]
        self.assertEqual(chatbot.chat('question', history), 'Free answer.')
        kw = self.free.call_args.kwargs
        self.assertEqual(len(kw['messages']), 21)
        self.assertEqual(kw['messages'][0]['content'], '10')
        self.assertIn('test context', kw['system'])
        self.assertFalse(kw['retry_on_5xx'])
        self.logged.assert_called_once()
        self.paid.assert_not_called()

    def test_cap_prevents_any_model_call(self):
        self.count.return_value = chatbot.MAX_CHATS_PER_DAY
        self.assertIn('Daily chat limit', chatbot.chat('question'))
        self.free.assert_not_called()

    def test_outage_has_no_paid_fallback_or_error_disclosure(self):
        self.free.side_effect = RuntimeError('private error detail')
        result = chatbot.chat('question')
        self.assertIn('temporarily unavailable', result)
        self.assertNotIn('private error detail', result)
        self.paid.assert_not_called()

    def test_empty_response(self):
        self.free.return_value = (' ', 'free', [], None)
        self.assertIn("couldn't generate", chatbot.chat('question'))
        self.paid.assert_not_called()


if __name__ == '__main__':
    unittest.main()
