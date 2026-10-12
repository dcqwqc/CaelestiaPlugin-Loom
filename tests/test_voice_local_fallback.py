"""Keep microphone capture as the first fallback when web Voice is unavailable."""
import unittest
from unittest.mock import Mock
from backend import TabbyBackend


class FakeVoice:
    def activate(self):
        return {'ok': True, 'active': False, 'composerReady': True}


class LocalVoiceFallbackTests(unittest.TestCase):
    def test_ready_composer_starts_local_mic_not_text_only(self):
        app = TabbyBackend.__new__(TabbyBackend)
        app._valid = lambda generation: True
        app._end_done = Mock(wait=lambda timeout: True)
        app._consume_prewarm = Mock(return_value={'ok': True})
        app._prepare_chat = Mock()
        app._engine_lock = __import__('threading').Lock()
        app.state = Mock()
        app.voice = FakeVoice()
        app._prime_response_cursor = Mock()
        app._start_local_voice = Mock(return_value=True)
        app._fallback_to_text_session = Mock(return_value=True)
        app._touch_session = Mock()
        app._clear_force_new_next = Mock()
        app._start_voice(1)
        app._start_local_voice.assert_called_once_with(1)
        app._fallback_to_text_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
