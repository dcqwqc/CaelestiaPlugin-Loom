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
