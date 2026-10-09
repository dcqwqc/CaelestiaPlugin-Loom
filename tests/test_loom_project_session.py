import unittest
from unittest.mock import Mock
from backend import TabbyBackend

PROJECT = 'g-p-6ac7add12a3c8191853411511fefc5da'
CHAT = 'https://chatgpt.com/g/' + PROJECT + '/c/01234567-0123-0123-0123-0123456789ab'


class LoomProjectSessionTests(unittest.TestCase):
    def make_backend(self):
        backend = TabbyBackend.__new__(TabbyBackend)
        backend.voice = Mock()
        backend._loom_project_id = lambda: PROJECT
        return backend

    def test_verified_new_project_composer(self):
        b = self.make_backend()
        b.voice.new_chat.return_value = {'ok': True}
        b.voice.loom_project_composer.return_value = {'ok': True}
        b.voice.status.return_value = {'ok': True, 'composerReady': True,
                                       'href': 'https://chatgpt.com/g/' + PROJECT + '/project'}
        self.assertEqual(b._new_loom_project_chat()['result'], 'loom-project-composer-ready')
        b.voice.new_chat.assert_called_once()
        b.voice.loom_project_composer.assert_called_once()

    def test_moves_existing_conversation_into_loom(self):
        b = self.make_backend()
        b.voice.status.side_effect = [
            {'href': 'https://chatgpt.com/c/01234567-0123-0123-0123-0123456789ab'},
            {'href': CHAT},
        ]
        b.voice.move_main_to_loom.return_value = {'ok': True}
        self.assertEqual(b._ensure_loom_project(), {'ok': True, 'href': CHAT})
        b.voice.move_main_to_loom.assert_called_once_with(PROJECT)

    def test_move_requires_verification_not_just_click(self):
        b = self.make_backend()
        b.voice.status.return_value = {'href': 'https://chatgpt.com/c/01234567-0123-0123-0123-0123456789ab'}
        b.voice.move_main_to_loom.return_value = {'ok': False, 'result': 'move-project-control-not-found'}
        self.assertEqual(b._ensure_loom_project()['result'], 'loom-project-move-unverified')

    def test_project_membership_is_exact(self):
        b = self.make_backend()
        self.assertTrue(b._chat_in_project(CHAT, PROJECT))
        self.assertFalse(b._chat_in_project(CHAT, 'g-p-other'))
        self.assertFalse(b._chat_in_project('https://chatgpt.com/c/01234567-0123-0123-0123-0123456789ab', PROJECT))

class CanonicalSessionRegressionTests(unittest.TestCase):
    def test_server_slugged_loom_project_url_matches(self):
        b = TabbyBackend.__new__(TabbyBackend)
        slugged = 'https://chatgpt.com/g/' + PROJECT + '-loom/c/01234567-0123-0123-0123-0123456789ab'
        self.assertTrue(b._chat_in_project(slugged, PROJECT))
        self.assertFalse(b._chat_in_project(slugged, PROJECT + '-other'))

    def test_error_banner_is_not_startup_ack(self):
        b = TabbyBackend.__new__(TabbyBackend)
        b.voice = Mock()
        b.voice.latest_response.return_value = {
            'ok': True, 'assistantCount':1,
            'assistantText': 'This response couldn’t load'}
        self.assertFalse(b._startup_acknowledged(0, ''))
        b.voice.latest_response.return_value = {
            'ok': True, 'assistantCount':1,
            'assistantText':'What should I take care of first?'}
        self.assertTrue(b._startup_acknowledged(0,''))

    def test_live_bridge_waits_for_canonical_route_to_settle(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'bridge/zen/.hey-tabby.uc.js').read_text()
        segment = source.split('async function openChat(rawUrl)', 1)[1].split('async function continueChat()', 1)[0]
        self.assertIn('stableSince', segment)
        self.assertIn('3300', segment)
        self.assertIn('title !== "new chat"', segment)
        self.assertNotIn('await sleep(1500)', segment)

class BridgeTimeoutSafetyTests(unittest.TestCase):
    def make_zen(self, path):
        import threading
        from tabby.zen import ZenClient
        z = ZenClient.__new__(ZenClient)
        z._lock = threading.Lock()
        z._last_seq = 0
        z.command = path
        z.ensure = lambda: True
        z._read = lambda: {}
        z._recycle_engine_window = lambda: self.fail('A transient timeout must never recycle active Voice')
        return z

    def test_status_timeout_does_not_recycle_voice(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            z = self.make_zen(Path(tmp) / 'command.json')
            result = z.call('status', timeout=.02)
            self.assertFalse(result['ok'])
            self.assertFalse(result['ambiguous'])
            self.assertEqual(result['result'], 'zen-bridge-timeout')

    def test_uncertain_send_is_never_retried_or_recycled(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            z = self.make_zen(Path(tmp) / 'command.json')
            result = z.call('send-text', timeout=.02, text='hello')
            self.assertFalse(result['ok'])
            self.assertTrue(result['ambiguous'])
