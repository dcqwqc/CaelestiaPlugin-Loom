import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from tabby.state import TabbyState

class StateTests(unittest.TestCase):
    def test_initially_hidden(self):
        state=TabbyState(True).snapshot()
        self.assertFalse(state["summoned"])
        self.assertEqual(state["audioLevel"],0.0)
        self.assertFalse(state["inputArmed"])

    def test_state_updates_are_separate(self):
        s=TabbyState(True)
        s.update(summoned=True,state="thinking",audioLevel=.5)
        snap=s.snapshot()
        self.assertTrue(snap["summoned"])
        self.assertEqual(snap["state"],"thinking")
        self.assertEqual(snap["audioLevel"],.5)

    def test_whiteboard_stays_owned_by_tabby(self):
        s=TabbyState(True)
        result=s.whiteboard({"command":"text","text":"hello","title":""})
        self.assertTrue(result["ok"])
        self.assertTrue(s.snapshot()["whiteboardVisible"])
        self.assertEqual(s.snapshot()["items"][0]["text"],"hello")

class BackendLifecycleTests(unittest.TestCase):
    def test_close_invalidates_workers(self):
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=__import__('threading').RLock(); b._generation=4
        b._voice_active=False; b._text_session=False; b._seen_voice_active=False
        b._hide_timer=None; b.debug=True
        b._end_done=__import__('threading').Event(); b._end_done.set()
        b._prewarm_ready=__import__('threading').Event()
        b._schedule_prewarm=lambda delay=0: None
        b.state=TabbyState(True); b.state.update(summoned=True)
        class Voice:
            def end(self): return {"ok":True}
            def hide(self): return {"ok":True}
        b.voice=Voice()
        b.close()
        self.assertTrue(b._end_done.wait(1.0))
        self.assertEqual(b._generation,5)
        self.assertFalse(b.state.snapshot()["summoned"])

    def test_stale_generation_is_rejected(self):
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=__import__('threading').RLock(); b._generation=9
        b.state=TabbyState(True); b.state.update(summoned=True)
        self.assertTrue(b._valid(9))
        self.assertFalse(b._valid(8))


class SessionPolicyTests(unittest.TestCase):
    def _backend(self, mode="smart", minutes=60):
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b.session_mode=mode
        b.smart_new_chat_minutes=minutes
        class Voice:
            def status(self): return {"ok":True,"href":"https://chatgpt.com/c/current"}
        b.voice=Voice()
        return b

    def test_session_policy_modes(self):
        b=self._backend("new")
        self.assertTrue(b._should_start_new())
        b=self._backend("continue")
        self.assertFalse(b._should_start_new())
        self.assertTrue(b._should_start_new(force_new=True))

    def test_smart_session_timeout(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._backend("smart", minutes=60)
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            with patch.object(backend_module, "SESSION_PATH", session):
                session.write_text(json.dumps({"last_used":time.time()-30*60}))
                self.assertFalse(b._should_start_new())
                session.write_text(json.dumps({"last_used":time.time()-61*60}))
                self.assertTrue(b._should_start_new())


class ReplyModeTests(unittest.TestCase):
    def _backend(self, mode):
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        import threading
        b.text_reply_mode=mode
        b._lock=threading.RLock()
        b._last_assistant_count=1
        b._last_assistant_text="old"
        b._text_reply_pending=(mode == "text-only")
        b._text_reply_seen_change=False
        b._response_stable_ticks=0
        b._next_response_poll=0
        b.state=TabbyState(True)
        b.state.update(summoned=True)
        class Voice:
            def latest_response(self):
                return {"ok":True,"assistantCount":2,"assistantText":"new answer"}
        b.voice=Voice()
        return b

    def test_text_reply_mode_gate(self):
        never=self._backend("never")
        never._monitor_text_reply({"working":False})
        self.assertFalse(never.state.snapshot()["whiteboardVisible"])
        typed=self._backend("text-only")
        typed._monitor_text_reply({"working":False})
        self.assertTrue(typed.state.snapshot()["whiteboardVisible"])
        always=self._backend("always")
        always._text_reply_pending=False
        always._monitor_text_reply({"working":False})
        self.assertTrue(always.state.snapshot()["whiteboardVisible"])

class MCPTests(unittest.TestCase):
    def test_expected_tools(self):
        from mcp_server import mcp
        names={t.name for t in mcp._tool_manager.list_tools()}
        for name in {"tabby_wake","tabby_close","tabby_send_text","whiteboard_write","companion_set_state"}:
            self.assertIn(name,names)

if __name__ == '__main__': unittest.main()
