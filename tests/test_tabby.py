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
        b.state=TabbyState(True); b.state.update(summoned=True)
        class Voice:
            def end(self): return {"ok":True}
            def hide(self): return {"ok":True}
        b.voice=Voice()
        b.close()
        self.assertEqual(b._generation,5)
        self.assertFalse(b.state.snapshot()["summoned"])

    def test_stale_generation_is_rejected(self):
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=__import__('threading').RLock(); b._generation=9
        b.state=TabbyState(True); b.state.update(summoned=True)
        self.assertTrue(b._valid(9))
        self.assertFalse(b._valid(8))

class MCPTests(unittest.TestCase):
    def test_expected_tools(self):
        from mcp_server import mcp
        names={t.name for t in mcp._tool_manager.list_tools()}
        for name in {"tabby_wake","tabby_close","tabby_send_text","whiteboard_write","companion_set_state"}:
            self.assertIn(name,names)

if __name__ == '__main__': unittest.main()
