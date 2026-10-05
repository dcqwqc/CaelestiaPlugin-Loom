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
        b._local_stt_proc=None; b._local_voice_fallback=False; b._local_voice_busy=False; b._local_stt_generation=0
        b._active_work_task_id=""; b._active_work_baseline_count=0; b._active_work_saw_working=False
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


class PreparedChatTests(unittest.TestCase):
    def _bare_backend(self):
        import threading
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b.session_mode="smart"
        b.smart_new_chat_minutes=60
        b.startup_prompt_enabled=True
        b.startup_prompt="startup"
        b._lock=threading.RLock()
        b._generation=1
        b.state=TabbyState(True)
        b.state.update(summoned=True)
        return b

    def test_clearing_force_new_preserves_prepared_slot(self):
        import json
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            session.write_text(json.dumps({
                "force_new_next":True,
                "prepared_chat_url":"https://chatgpt.com/c/prepared",
            }))
            with patch.object(backend_module, "SESSION_PATH", session):
                b._clear_force_new_next()
                data=json.loads(session.read_text())
                self.assertFalse(data["force_new_next"])
                self.assertEqual(data["prepared_chat_url"], "https://chatgpt.com/c/prepared")

    def test_foreground_reuses_prepared_chat_without_resending_startup(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        opened=[]
        class Voice:
            def open_chat(self,url):
                opened.append(url)
                return {"ok":True,"href":url,"fresh":False}
            def new_chat(self):
                raise AssertionError("prepared slot should be reused")
            def continue_chat(self):
                raise AssertionError("prepared slot should be reused")
            def status(self):
                return {"ok":True,"href":"https://chatgpt.com/c/prepared"}
        b.voice=Voice()
        sent=[]
        b._send_startup_prompt=lambda *a,**kw: sent.append(True) or True
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            session.write_text(json.dumps({
                "last_used":time.time(),
                "force_new_next":True,
                "prepared_chat_url":"https://chatgpt.com/c/prepared",
            }))
            with patch.object(backend_module, "SESSION_PATH", session):
                result,new_chat=b._prepare_chat(1)
                self.assertTrue(result["ok"])
                self.assertTrue(new_chat)
                self.assertEqual(opened,["https://chatgpt.com/c/prepared"])
                self.assertEqual(sent,[])
                self.assertEqual(json.loads(session.read_text()).get("prepared_chat_url"),"")


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


class LocalVoiceFallbackTests(unittest.TestCase):
    def test_local_voice_send_uses_same_chat_and_read_aloud(self):
        import threading
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=threading.RLock()
        b._generation=7
        b._local_voice_fallback=True
        b._local_voice_busy=False
        b._local_stt_proc=None
        b._audio_level=0.0
        b._last_assistant_count=1
        b._last_assistant_text="old"
        b.state=TabbyState(True)
        b.state.update(summoned=True)
        b._prime_response_cursor=lambda: None
        calls=[]
        class Voice:
            def send_text(self,text): calls.append(("send",text)); return {"ok":True}
            def latest_response(self): return {"ok":True,"assistantCount":2,"assistantText":"hello back"}
            def status(self): return {"ok":True,"working":False}
            def read_aloud(self): calls.append(("read",)); return {"ok":True}
        b.voice=Voice()
        with patch('backend.time.sleep', return_value=None):
            b._local_voice_send("hello",7)
        self.assertIn(("send","hello"),calls)
        self.assertIn(("read",),calls)
        self.assertEqual(b._last_assistant_text,"hello back")
        self.assertTrue(b.state.snapshot()["whiteboardVisible"])
        self.assertFalse(b._local_voice_busy)


class FnHotkeyTests(unittest.TestCase):
    def test_double_tap_triggers_once(self):
        from tabby.fn_hotkey import FnHotkeyMonitor
        hits=[]
        m=FnHotkeyMonitor(lambda: hits.append(1), double_tap_ms=350)
        with patch('tabby.fn_hotkey.time.monotonic', side_effect=[1.0, 1.2, 2.0, 2.6]):
            m._tap(); m._tap(); m._tap(); m._tap()
        self.assertEqual(hits, [1])

    def test_mirai_physical_fn_is_not_exposed(self):
        from tabby.fn_hotkey import FnHotkeyMonitor
        names=[name for _,name in FnHotkeyMonitor._physical_candidates()]
        self.assertFalse(any('ydotool' in n.lower() or 'virtual' in n.lower() for n in names))


class LeftAltHotkeyTests(unittest.TestCase):
    def test_double_left_alt_standalone_taps(self):
        from tabby.fn_hotkey import LeftAltHotkeyMonitor, KEY_LEFTALT
        hits=[]
        m=LeftAltHotkeyMonitor(lambda: hits.append(1), double_tap_ms=350)
        with patch('tabby.fn_hotkey.time.monotonic', side_effect=[1.00,1.10,1.10,1.25,1.35,1.35]):
            m._handle_key(KEY_LEFTALT,1); m._handle_key(KEY_LEFTALT,0)
            m._handle_key(KEY_LEFTALT,1); m._handle_key(KEY_LEFTALT,0)
        self.assertEqual(hits,[1])

    def test_alt_chord_does_not_count_as_tap(self):
        from tabby.fn_hotkey import LeftAltHotkeyMonitor, KEY_LEFTALT
        hits=[]
        m=LeftAltHotkeyMonitor(lambda: hits.append(1), double_tap_ms=350)
        # Alt down, Tab down, Alt up; then one clean Alt tap. This must not
        # produce a double-tap because the first Alt was a modifier chord.
        with patch('tabby.fn_hotkey.time.monotonic', side_effect=[2.00,2.04,2.12,2.30,2.40,2.40]):
            m._handle_key(KEY_LEFTALT,1)
            m._handle_key(15,1)  # KEY_TAB
            m._handle_key(KEY_LEFTALT,0)
            m._handle_key(KEY_LEFTALT,1); m._handle_key(KEY_LEFTALT,0)
        self.assertEqual(hits,[])

    def test_physical_left_alt_is_available_on_mirai(self):
        from tabby.fn_hotkey import LeftAltHotkeyMonitor
        candidates=LeftAltHotkeyMonitor._physical_candidates()
        self.assertTrue(any('AT Translated Set 2 keyboard' in name for _,name in candidates))

class MCPTests(unittest.TestCase):
    def test_expected_tools(self):
        from mcp_server import mcp
        names={t.name for t in mcp._tool_manager.list_tools()}
        for name in {"tabby_wake","tabby_close","tabby_send_text","whiteboard_write","companion_set_state"}:
            self.assertIn(name,names)

if __name__ == '__main__': unittest.main()
