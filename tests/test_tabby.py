import os
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from tabby.state import TabbyState

class StateTests(unittest.TestCase):
    def test_initially_hidden(self):
        state=TabbyState(True).snapshot()
        self.assertFalse(state["summoned"])
        self.assertFalse(state["voiceActive"])
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
        b.state=TabbyState(True); b.state.update(summoned=True,voiceActive=True)
        class Voice:
            def end(self): return {"ok":True,"active":False}
            def status(self): return {"ok":True,"active":False}
            def hide(self): return {"ok":True}
        b.voice=Voice()
        b.close()
        self.assertTrue(b._end_done.wait(1.0))
        self.assertEqual(b._generation,5)
        self.assertFalse(b.state.snapshot()["summoned"])
        self.assertFalse(b.state.snapshot()["voiceActive"])

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

    def test_force_new_flag_is_consumed_when_chat_is_prepared(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        url="https://chatgpt.com/c/replacement"
        class Voice:
            def new_chat(self):
                return {"ok":True,"href":url,"fresh":False}
            def continue_chat(self):
                raise AssertionError("force_new_next must create exactly one replacement chat")
            def status(self):
                return {"ok":True,"href":url,"composerReady":True}
        b.voice=Voice()
        b._send_startup_prompt=lambda *a,**kw: True
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            session.write_text(json.dumps({
                "last_used":time.time(),
                "force_new_next":True,
                "prepared_chat_url":"",
                "last_chat_url":"https://chatgpt.com/c/old",
            }))
            with patch.object(backend_module,"SESSION_PATH",session):
                result,new_chat=b._prepare_chat(1)
                data=json.loads(session.read_text())
        self.assertTrue(result["ok"])
        self.assertTrue(new_chat)
        self.assertFalse(data["force_new_next"])
        self.assertEqual(data["last_chat_url"],url)

    def test_foreground_reuses_prepared_chat_without_resending_startup(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        opened=[]; continued=[]
        class Voice:
            def open_chat(self,url):
                opened.append(url)
                return {"ok":True,"href":url,"fresh":False}
            def new_chat(self):
                raise AssertionError("prepared slot should be reused")
            def continue_chat(self):
                continued.append(True)
                return {"ok":True,"href":"https://chatgpt.com/c/prepared","fresh":False,"composerReady":True,"ready":True,"working":False}
            def status(self):
                return {"ok":True,"href":"https://chatgpt.com/c/prepared","composerReady":True}
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
                self.assertEqual(opened,[])
                self.assertEqual(continued,[True])
                self.assertEqual(sent,[])
                self.assertEqual(json.loads(session.read_text()).get("prepared_chat_url"),"")

    def test_resume_helper_reloads_same_url_only_when_composer_is_stalled(self):
        b=self._bare_backend()
        url="https://chatgpt.com/c/stalled"
        calls=[]
        class Voice:
            def status(self): return {"ok":True,"href":url,"composerReady":False,"working":False,"connectionInterrupted":False}
            def continue_chat(self): raise AssertionError("stalled no-composer page should not burn continue timeout")
            def open_chat(self,u): calls.append(u); return {"ok":True,"href":u,"composerReady":True}
        b.voice=Voice()
        result=b._resume_chat_in_place(url)
        self.assertTrue(result["ok"]); self.assertEqual(calls,[url])

    def test_resume_helper_never_reloads_same_open_url(self):
        b=self._bare_backend()
        url="https://chatgpt.com/c/prewarmed"
        calls=[]
        class Voice:
            def status(self): calls.append("status"); return {"ok":True,"href":url,"composerReady":True}
            def continue_chat(self): calls.append("continue"); return {"ok":True,"href":url,"composerReady":True}
            def open_chat(self,u): calls.append(("open",u)); return {"ok":True,"href":u}
        b.voice=Voice()
        result=b._resume_chat_in_place(url)
        self.assertTrue(result["ok"]); self.assertEqual(calls,["status","continue"])

    def test_same_open_resume_url_uses_continue_without_reload(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        calls=[]
        url="https://chatgpt.com/c/already-open"
        class Voice:
            def status(self): return {"ok":True,"href":url,"composerReady":True}
            def continue_chat(self): calls.append("continue"); return {"ok":True,"href":url,"fresh":False,"composerReady":True}
            def open_chat(self,u): raise AssertionError("same open conversation must not reload")
            def new_chat(self): raise AssertionError("smart continue must not create a new chat")
        b.voice=Voice()
        b._send_startup_prompt=lambda *a,**kw: (_ for _ in ()).throw(AssertionError("startup must not resend"))
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            session.write_text(json.dumps({"last_used":time.time(),"force_new_next":False,"last_chat_url":url}))
            with patch.object(backend_module,"SESSION_PATH",session):
                result,new_chat=b._prepare_chat(1)
        self.assertTrue(result["ok"]); self.assertFalse(new_chat); self.assertEqual(calls,["continue"])

    def test_transient_actor_gap_stabilizes_same_chat_without_reload(self):
        import json, time
        from pathlib import Path
        import backend as backend_module
        b=self._bare_backend()
        url="https://chatgpt.com/c/already-open"
        calls=[]
        class Voice:
            def status(self): calls.append("status"); return {"ok":False,"result":"actor-unavailable"}
            def continue_chat(self): calls.append("continue"); return {"ok":True,"href":url,"fresh":False,"composerReady":True,"ready":True,"working":False}
            def open_chat(self,u): raise AssertionError("transient actor gap must not reload the same chat")
            def new_chat(self): raise AssertionError("must not create a new chat")
        b.voice=Voice()
        b._send_startup_prompt=lambda *a,**kw: True
        with tempfile.TemporaryDirectory() as tmp:
            session=Path(tmp)/"session.json"
            session.write_text(json.dumps({"last_used":time.time(),"force_new_next":False,"last_chat_url":url}))
            with patch.object(backend_module,"SESSION_PATH",session):
                result,new_chat=b._prepare_chat(1)
        self.assertTrue(result["ok"]); self.assertFalse(new_chat)
        self.assertEqual(calls,["status","continue"])

    def test_prewarm_match_ignores_last_used_stamp_drift(self):
        import threading
        b=self._bare_backend()
        b._prewarm_ready=threading.Event(); b._prewarm_ready.set()
        b._prewarm_lock=threading.RLock()
        b._prewarm_new=False
        b._prewarm_session_stamp=1.0
        b._should_start_new=lambda force_new=False: False
        b._session_stamp=lambda: 999.0
        self.assertTrue(b._prewarm_matches(False))


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


class VoiceMicRecoveryTests(unittest.TestCase):
    def _backend(self, mic_result):
        import threading
        from backend import TabbyBackend
        b=TabbyBackend.__new__(TabbyBackend)
        b._lock=threading.RLock()
        b._generation=3
        b.state=TabbyState(True)
        b.state.update(summoned=True)
        calls=[]
        class Voice:
            def mic_on(self):
                calls.append("mic-on")
                return dict(mic_result)
            def status(self):
                calls.append("status")
                return dict(mic_result)
        b.voice=Voice()
        return b,calls

    def test_muted_voice_is_unmuted_before_fallback(self):
        live={"kind":"audio","readyState":"live","enabled":True}
        b,calls=self._backend({"ok":True,"active":True,"micMuted":False,"audioTracks":[live]})
        b._start_local_voice=lambda generation: (_ for _ in ()).throw(AssertionError("fallback should not start"))
        self.assertEqual(b._recover_voice_mic(3,{"ok":True,"active":True,"micMuted":True,"audioTracks":[]}),"voice")
        self.assertEqual(calls,["mic-on"])

    def test_failed_unmute_uses_local_fallback(self):
        b,calls=self._backend({"ok":False,"active":True,"micMuted":True,"audioTracks":[]})
        fallback=[]
        b._start_local_voice=lambda generation: fallback.append(generation) or True
        with patch('backend.time.sleep', return_value=None):
            self.assertEqual(b._recover_voice_mic(3,{"ok":True,"active":True,"micMuted":True,"audioTracks":[]}),"fallback")
        self.assertGreaterEqual(calls.count("mic-on"),1)
        self.assertGreaterEqual(calls.count("status"),1)
        self.assertEqual(fallback,[3])

    def test_unmuted_without_live_track_is_not_voice_ready(self):
        b,calls=self._backend({"ok":True,"active":True,"micMuted":False,"audioTracks":[]})
        fallback=[]
        b._start_local_voice=lambda generation: fallback.append(generation) or True
        with patch('backend.time.sleep', return_value=None):
            self.assertEqual(b._recover_voice_mic(3,{"ok":True,"active":True,"micMuted":False,"audioTracks":[]}),"fallback")
        self.assertEqual(fallback,[3])


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


class ZenClientFocusTests(unittest.TestCase):
    def _client(self, debug=False):
        from tabby.zen import ZenClient
        client=ZenClient.__new__(ZenClient)
        client.debug=debug
        client._voice_audio_restore=[]
        client._voice_audio_generation=0
        client._workspace_visibility_state=None
        client._route_lock=threading.RLock()
        return client

    def test_cursor_restore_uses_hyprland_cursor_dispatcher(self):
        client=self._client(False)
        calls=[]
        class R:
            returncode=0
            stdout="ok"
        with patch('tabby.zen.subprocess.run', side_effect=lambda args, **kwargs: (calls.append(args) or R())):
            self.assertTrue(client._restore_cursor((123,456)))
        dispatches=[c for c in calls if len(c) >= 3 and c[0:2] == ['hyprctl','dispatch']]
        self.assertTrue(dispatches)
        self.assertIn('hl.dsp.cursor.move({ x = 123, y = 456 })',dispatches[0][2])

    def test_workspace_visibility_only_reveals_parked_open_special(self):
        client=self._client(False)
        seen=[]
        client._set_engine_opacity=lambda value: seen.append(value) or True
        client._engine_clients=lambda: [{"address":"0xengine","workspace":{"name":"special:tabby"}}]
        client._tabby_special_open=lambda: False
        client.sync_workspace_visibility(force=True)
        self.assertEqual(seen[-1],0)
        client._tabby_special_open=lambda: True
        client.sync_workspace_visibility(force=True)
        self.assertEqual(seen[-1],1)
        client._engine_clients=lambda: [{"address":"0xengine","workspace":{"name":"1"}}]
        client.sync_workspace_visibility(force=True)
        self.assertEqual(seen[-1],0)

    def test_status_is_read_only_and_never_routes_engine(self):
        client=self._client(False)
        client.call=lambda command, **kwargs: {"ok":True,"command":command}
        client._route_engine_window=lambda visible: (_ for _ in ()).throw(AssertionError("status must never mutate workspace"))
        result=client.status()
        self.assertTrue(result["ok"]); self.assertEqual(result["command"],"status")

    def test_hidden_activate_restores_previous_hypr_focus(self):
        client=self._client(False)
        events=[]
        client._user_focus_address=lambda: "0xabc"
        client._cursor_position=lambda: (321,654)
        client._begin_cursor_no_warps_guard=lambda: (False,(events.append(("no-warps",True)) or True))
        client._end_cursor_no_warps_guard=lambda value: events.append(("no-warps",value)) or True
        client._restore_cursor=lambda pos: events.append(("cursor",pos)) or True
        client._read=lambda: {}
        client._route_voice_audio=lambda title: events.append(("audio",title)) or True
        client._stage_hidden_engine=lambda: events.append(("stage-hidden",)) or True
        client._park_hidden_engine=lambda address='': events.append(("park-hidden",address))
        client.call=lambda command, **kwargs: events.append(("call",command,kwargs.get("debug"))) or {"ok":True}
        client._route_engine_window=lambda visible: events.append(("route",visible))
        client._focus_address=lambda address: events.append(("focus",address)) or True
        self.assertTrue(client.activate()["ok"])
        self.assertEqual(events,[
            ("no-warps",True),
            ("stage-hidden",),
            ("call","activate",False),
            ("park-hidden","0xabc"),
            ("cursor",(321,654)),
            ("no-warps",False),
        ])

    def test_debug_activate_keeps_engine_focus(self):
        client=self._client(True)
        events=[]
        client._user_focus_address=lambda: (_ for _ in ()).throw(AssertionError("debug must not capture user focus"))
        client._read=lambda: {}
        client._stage_hidden_engine=lambda: (_ for _ in ()).throw(AssertionError("debug must not stage hidden engine"))
        client.call=lambda command, **kwargs: events.append(("call",command,kwargs.get("debug"))) or {"ok":True}
        client._route_engine_window=lambda visible: events.append(("route",visible))
        client._focus_address=lambda address: (_ for _ in ()).throw(AssertionError("debug must not restore focus"))
        self.assertTrue(client.activate()["ok"])
        self.assertEqual(events,[("call","activate",True),("route",True)])

    def test_engine_client_detection_covers_hidden_workspace(self):
        from tabby.zen import ZenClient
        self.assertTrue(ZenClient._is_tabby_engine_client({"title":"Tabby Engine · ChatGPT","workspace":{"name":"special:tabby"}}))
        self.assertTrue(ZenClient._is_tabby_engine_client({"title":"ChatGPT","workspace":{"name":"special:tabby"}}))
        self.assertFalse(ZenClient._is_tabby_engine_client({"title":"ChatGPT","workspace":{"name":"1"}}))


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

class DisplayTests(unittest.TestCase):
    def _state(self):
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            s=TabbyState(True)
        s.publish=lambda: None
        return s

    def test_display_upserts_by_id_and_merges_fields(self):
        s=self._state()
        s.whiteboard({"command":"display","items":[{"type":"progress","id":"build","label":"Build","value":.2}]})
        s.whiteboard({"command":"display","items":[{"id":"build","value":.8}]})
        items=s.snapshot()["items"]
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]["label"],"Build")
        self.assertEqual(items[0]["value"],.8)

    def test_choice_answer_and_remove(self):
        s=self._state()
        s.whiteboard({"command":"display","items":[{"type":"choice","id":"q","label":"Go?","options":["Yes","No"]}]})
        self.assertFalse(s.whiteboard({"command":"choose","item_id":"q","option":"Maybe"})["ok"])
        self.assertTrue(s.whiteboard({"command":"choose","item_id":"q","option":"Yes"})["ok"])
        self.assertEqual(s.ui_snapshot()["items"][0]["selected"],"Yes")
        self.assertEqual(s.whiteboard({"command":"ui-remove","ids":["q"]})["removed"],1)
        self.assertFalse(s.snapshot()["whiteboardVisible"])

    def test_unknown_widget_type_is_kept_generically(self):
        s=self._state()
        result=s.whiteboard({"command":"display","items":[{"type":"sparkline","points":[1,2],"title":"CPU"}]})
        self.assertTrue(result["ok"])
        self.assertEqual(s.snapshot()["items"][0]["type"],"sparkline")
        self.assertFalse(s.whiteboard({"command":"display","items":[{"type":"shape","kind":"star"}]})["ok"])

    def test_replace_mode_and_item_cap(self):
        s=self._state()
        s.whiteboard({"command":"display","items":[{"type":"text","text":str(i)} for i in range(40)]})
        self.assertEqual(len(s.snapshot()["items"]),32)
        s.whiteboard({"command":"display","mode":"replace","items":[{"type":"divider"}]})
        self.assertEqual([i["type"] for i in s.snapshot()["items"]],["divider"])


class WorkingStoreTests(unittest.TestCase):
    def test_manual_task_create_complete_reopen_and_reload(self):
        from pathlib import Path
        from tabby.working import WorkingStore
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"working.json"
            store=WorkingStore(path)
            task=store.create("Ship Tabby MCP",summary="wiring",progress=.3)
            self.assertEqual(task["kind"],"manual")
            self.assertEqual(store.update(task["id"],status="blocked")["status"],"blocked")
            done=store.complete(task["id"],summary="shipped")
            self.assertEqual((done["status"],done["progress"]),("done",1.0))
            again=store.reopen(task["id"])
            self.assertEqual((again["status"],again["progress"],again["completedAt"]),("working",0.0,0.0))
            reloaded=WorkingStore(path).get(task["id"])
            self.assertEqual(reloaded["kind"],"manual")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode),0o600)


class MCPTests(unittest.TestCase):
    def test_expected_tools(self):
        import tabby_mcp
        names={t["name"] for t in tabby_mcp.tool_list()}
        for name in {"tabby_show","tabby_hide","tabby_clear","tabby_write","tabby_progress","tabby_choice",
                     "tabby_shape","tabby_card","tabby_display","tabby_task_create","tabby_task_pin_current",
                     "tabby_task_update","tabby_task_done","tabby_task_reopen"}:
            self.assertIn(name,names)
        self.assertFalse(any("shell" in n or "exec" in n for n in names))

    def test_rpc_initialize_list_and_call(self):
        import tabby_mcp
        init=tabby_mcp.handle_rpc({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"],"2025-03-26")
        self.assertIsNone(tabby_mcp.handle_rpc({"jsonrpc":"2.0","method":"notifications/initialized"}))
        sent=[]
        with patch.object(tabby_mcp,"send_command",lambda payload,timeout=3.0: sent.append(payload) or {"ok":True,"items":[{"id":"x"}]}):
            result=tabby_mcp.handle_rpc({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"tabby_progress","arguments":{"value":.5,"label":"Build","id":"b"}}})
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(sent[0],{"command":"display","items":[{"type":"progress","value":.5,"label":"Build","id":"b"}],"mode":"append"})
        unknown=tabby_mcp.handle_rpc({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"nope"}})
        self.assertEqual(unknown["error"]["code"],-32602)

    def test_http_requires_token(self):
        import json, urllib.request, urllib.error, tabby_mcp
        from http.server import ThreadingHTTPServer
        tabby_mcp.Handler.token="t"*40
        httpd=ThreadingHTTPServer(("127.0.0.1",0),tabby_mcp.Handler)
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        base=f"http://127.0.0.1:{httpd.server_address[1]}"
        body=json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/list"}).encode()
        def post(path,headers=None):
            req=urllib.request.Request(base+path,data=body,headers={"Content-Type":"application/json",**(headers or {})})
            try:
                with urllib.request.urlopen(req,timeout=5) as r: return r.status,json.loads(r.read())
            except urllib.error.HTTPError as e: return e.code,None
        try:
            self.assertEqual(post("/mcp")[0],404)
            self.assertEqual(post("/mcp/wrong")[0],404)
            code,data=post("/mcp/"+"t"*40)
            self.assertEqual(code,200)
            self.assertTrue(data["result"]["tools"])
            self.assertEqual(post("/mcp",{"Authorization":"Bearer "+"t"*40})[0],200)
            # a rejected request on a kept-alive connection must not poison the next one
            import http.client
            conn=http.client.HTTPConnection("127.0.0.1",httpd.server_address[1],timeout=5)
            conn.request("POST","/mcp/wrong",body=b"{}",headers={"Content-Type":"application/json"})
            r=conn.getresponse(); r.read(); self.assertEqual(r.status,404)
            conn.request("POST","/mcp/"+"t"*40,body=body,headers={"Content-Type":"application/json"})
            r=conn.getresponse(); r.read(); self.assertEqual(r.status,200)
            conn.close()
        finally:
            httpd.shutdown()

if __name__ == '__main__': unittest.main()
