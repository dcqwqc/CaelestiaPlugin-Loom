"""Mobile one-use action tokens and ntfy publishing tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tabby.notifications import NotificationStore, deliver
from tabby.phone_actions import choose, prepare


class PhoneActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.tmp.name) / 'notifs.sqlite3')
        self.item = self.store.create(title='Pick one', kind='choice', options=['A', 'B', 'C'])
        self.base = 'https://mirai.tail99b431.ts.net/loom-phone'

    def tearDown(self):
        self.tmp.cleanup()

    def test_tokens_record_selected_option_and_reject_replay(self):
        actions = prepare(self.item, self.store, self.base)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[1]['label'], 'B')
        self.assertEqual(actions[1]['method'], 'POST')
        selected = choose(actions[1]['url'].rsplit('/', 1)[-1], self.store)
        self.assertEqual(selected['status'], 'answered')
        self.assertEqual(selected['response'], 'B')
        with self.assertRaises(ValueError):
            choose(actions[1]['url'].rsplit('/', 1)[-1], self.store)
        with self.assertRaises(ValueError):
            choose(actions[0]['url'].rsplit('/', 1)[-1], self.store)

    def test_reject_invalid_and_expired_token(self):
        actions = prepare(self.item, self.store, self.base)
        with self.assertRaises(ValueError):
            choose('not-valid', self.store)
        with self.store._db() as db:
            db.execute('UPDATE phone_actions SET expires_at=0')
        with self.assertRaises(ValueError):
            choose(actions[0]['url'].rsplit('/', 1)[-1], self.store)
        self.assertEqual(self.store.get(self.item['id'])['status'], 'pending')

    def test_no_action_for_non_decisions(self):
        note = self.store.create(title='Only status')
        with self.assertRaises(ValueError):
            prepare(note, self.store, self.base)

    def test_excess_options_cannot_be_misrepresented(self):
        note = self.store.create(title='Four', kind='choice', options=['A', 'B', 'C', 'D'])
        with self.assertRaises(ValueError):
            prepare(note, self.store, self.base)

    def test_callback_url_must_be_https(self):
        with self.assertRaises(ValueError):
            prepare(self.item, self.store, 'http://127.0.0.1:8767')

    def test_publishing_branded_buttons(self):
        class FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
        sent = []
        def fake_send(req, timeout=0):
            sent.append(json.loads(req.data))
            return FakeResponse()
        with patch.dict(os.environ, {'LOOM_NTFY_URL': 'https://ntfy.sh/testing',
                                      'LOOM_PHONE_ACTION_BASE': self.base,
                                      'LOOM_NTFY_ICON_URL': 'https://example.org/loom.png'}, clear=False), \
             patch('tabby.notifications.urllib.request.urlopen', side_effect=fake_send):
            result = deliver(self.item, self.store)
        self.assertEqual(result['phone'], 'accepted_by_provider')
        self.assertEqual(sent[0]['topic'], 'testing')
        self.assertEqual(sent[0]['title'], 'Loom · Pick one')
        self.assertEqual(len(sent[0]['actions']), 3)
        self.assertEqual(sent[0]['icon'], 'https://example.org/loom.png')

    def test_no_mobile_send_without_callback_config(self):
        with patch.dict(os.environ, {'LOOM_NTFY_URL':'https://ntfy.sh/testing',
                                      'LOOM_PHONE_ACTION_BASE':''}, clear=False):
            result = deliver(self.item, self.store)
        self.assertEqual(result['phone'], 'callback_not_configured')


if __name__ == '__main__':
    unittest.main()
