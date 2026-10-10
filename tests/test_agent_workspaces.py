import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tabby import agent_workspaces as aw
from tabby.agent_workspaces import AgentWorkspaceService, GuiError


class FakeHelper:
    def __init__(self, runtime, ws):
        self.runtime, self.ws_id = runtime, ws["id"]
        self.calls = []
        self.width, self.height = ws["width"], ws["height"]
        self.pointer = [ws["width"] // 2, ws["height"] // 2]

    def alive(self):
        return True

    def close(self):
        pass

    def call(self, op, timeout=10.0, **args):
        self.calls.append((op, args))
        self.runtime.ops.append((self.ws_id, op, args))
        if self.runtime.on_call:
            self.runtime.on_call(op, args)
        if op == "move":
            self.pointer = [args["x"], args["y"]]
            return {"ok": True, "pointer": self.pointer}
        if op == "pointer":
            return {"ok": True, "pointer": self.pointer}
        if op == "windows":
            return {"ok": True, "windows": [{"id": 1, "title": "app"}]}
        if op == "type":
            return {"ok": True, "typed": len(args["text"])}
        if op == "capture":
            if args.get("preview"):
                Path(args["preview"]).write_bytes(b"png")
            if args.get("path"):
                Path(args["path"]).write_bytes(b"png")
            return {"ok": True, "path": args.get("path", ""), "imageWidth": 10, "imageHeight": 10}
        if op == "bidi-eval":
            return {"ok": True, "value": self.runtime.page_value}
        if op == "bidi-navigate":
            return {"ok": True, "url": args["url"]}
        return {"ok": True}


class FakeRuntime:
    def __init__(self):
        self.displays = {}
        self.started = 0
        self.ops = []
        self.swept = []
        self.on_call = None
        self.page_value = {"found": True, "x": 100, "y": 50, "obscured": False}
        self.next_pid = 5000

    def capabilities(self, cfg):
        return {"desktop": {"available": True, "reason": ""}, "browser": {"available": True, "reason": ""},
                "user_desktop_input": {"available": False, "reason": "single seat"}}

    def start_display(self, ws):
        self.started += 1
        self.next_pid += 1
        self.displays[ws["id"]] = True
        return {"display": f":{self.started}", "xvfb_pid": self.next_pid, "xauth": "/x"}

    def helper(self, ws, spawn=True):
        return FakeHelper(self, ws)

    def spawn_app(self, ws, argv):
        self.next_pid += 1
        return self.next_pid

    def spawn_browser(self, ws, cfg, url):
        self.next_pid += 1
        return {"browser_pid": os.getpid(), "bidi_port": 1}

    def display_alive(self, ws):
        return self.displays.get(ws["id"], False) and "xvfb_pid" in ws

    def kill_pid_group(self, pid):
        pass

    def sweep(self, ws_id, grace=2.0):
        self.swept.append(ws_id)
        self.displays[ws_id] = False
        return []

    def remove_dirs(self, ws, keep_profile):
        pass


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = {"LOOM_AGENT_INPUT_STATE": str(root / "state"), "LOOM_AGENT_INPUT_RUNTIME": str(root / "run" / "ai"),
               "LOOM_AGENT_INPUT_CONFIG": str(root / "cfg.json")}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.root = root
        self.rt = FakeRuntime()
        self.clock = Clock()
        self.svc = AgentWorkspaceService(self.rt, clock=self.clock)

    def acquire(self, agent="claude-a1", key="k1", kind="desktop", **kw):
        r = self.svc.acquire(agent_id=agent, request_key=key, kind=kind, **kw)
        return r["workspace"]["id"], r["token"], r

    def creds(self, ws, tok, agent="claude-a1"):
        return {"workspace_id": ws, "agent_id": agent, "token": tok}


class ColorTests(ServiceTestCase):
    def test_colors_are_stable_distinct_and_persistent(self):
        a = self.svc.agent_profile("claude-a1")["color"]
        b = self.svc.agent_profile("codex-b2")["color"]
        self.assertNotEqual(a, b)
        self.assertIn(a, [c for _, c in aw.PALETTE])
        again = AgentWorkspaceService(self.rt, clock=self.clock)
        self.assertEqual(again.agent_profile("claude-a1")["color"], a)
        self.assertEqual(stat.S_IMODE(os.stat(self.root / "cfg.json").st_mode), 0o600)

    def test_chosen_color_by_name_or_hex_and_validation(self):
        self.svc.set_appearance("claude-a1", color="peach", name="Claude")
        self.assertEqual(self.svc.agent_profile("claude-a1")["color"], aw.PALETTE_BY_NAME["peach"])
        self.svc.set_appearance("claude-a1", color="#aabbcc")
        self.assertEqual(self.svc.agent_profile("claude-a1")["color"], "#AABBCC")
        with self.assertRaises(GuiError) as ctx:
            self.svc.set_appearance("claude-a1", color="red; rm -rf")
        self.assertEqual(ctx.exception.code, "invalid")

    def test_assignment_avoids_taken_colors(self):
        taken = [c for _, c in aw.PALETTE[:-1]]
        self.assertEqual(aw.assign_color("anything", taken), aw.PALETTE[-1][1])


class LifecycleTests(ServiceTestCase):
    def test_acquire_is_idempotent_per_agent_and_key(self):
        ws1, _, r1 = self.acquire()
        ws2, _, r2 = self.acquire()
        self.assertEqual(ws1, ws2)
        self.assertEqual((r1["result"], r2["result"]), ("created", "existing"))
        self.assertEqual(self.rt.started, 1)
        ws3, _, _ = self.acquire(agent="codex-b2")
        self.assertNotEqual(ws1, ws3)

    def test_kind_conflict_and_limits(self):
        self.acquire()
        with self.assertRaises(GuiError) as ctx:
            self.acquire(kind="browser")
        self.assertEqual(ctx.exception.code, "invalid")
        self.svc.cfg["max_workspaces"] = 2
        self.acquire(key="k2")
        with self.assertRaises(GuiError) as ctx:
            self.acquire(key="k3")
        self.assertEqual(ctx.exception.code, "limit")

    def test_no_secrets_in_public_views_or_overlay(self):
        ws, tok, r = self.acquire()
        blob = json.dumps(r["workspace"]) + json.dumps(self.svc.list()) + json.dumps(self.svc.overlay_state())
        self.assertNotIn(tok, blob)
        self.assertNotIn("token_hashes", blob)
        state = (self.root / "state" / "workspaces.json").read_text()
        self.assertNotIn(tok, state)  # only hashes are persisted

    def test_release_sweeps_and_removes_from_overlay(self):
        ws, tok, _ = self.acquire()
        self.svc.release(**self.creds(ws, tok))
        self.assertIn(ws, self.rt.swept)
        self.assertEqual(self.svc.overlay_state()["agents"], [])
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), x=1, y=1)
        self.assertEqual(ctx.exception.code, "forbidden")  # tokens are revoked on release

    def test_release_owner_by_ai_session(self):
        ws, _, _ = self.acquire(ai_session="claude-72b003")
        other, _, _ = self.acquire(agent="codex-b2", ai_session="codex-1")
        out = self.svc.release_owner(ai_session="claude-72b003")
        self.assertEqual(out["released"], [ws])
        self.assertEqual(self.svc.workspaces[other]["state"], "ready")


class AuthorizationTests(ServiceTestCase):
    def test_other_agents_cannot_inject(self):
        ws, tok, _ = self.acquire()
        _, tok_b, _ = self.acquire(agent="codex-b2")
        for agent, token in (("codex-b2", tok_b), ("codex-b2", tok), ("claude-a1", tok_b), ("claude-a1", "")):
            with self.assertRaises(GuiError) as ctx:
                self.svc.type_text(workspace_id=ws, agent_id=agent, token=token, text="x")
            self.assertEqual(ctx.exception.code, "forbidden")
        self.assertFalse([o for o in self.rt.ops if o[1] == "type"])
        audit = (self.root / "state" / "audit.jsonl").read_text()
        self.assertIn('"op": "authorize"', audit)

    def test_attach_requires_ownership_or_grant_and_turn_taking(self):
        ws, tok, _ = self.acquire()
        with self.assertRaises(GuiError):
            self.svc.attach(workspace_id=ws, agent_id="codex-b2")
        self.svc.grant(**self.creds(ws, tok), grantee="codex-b2")
        tok_b = self.svc.attach(workspace_id=ws, agent_id="codex-b2")["token"]
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok_b, "codex-b2"), x=5, y=5)
        self.assertEqual(ctx.exception.code, "busy")
        self.svc.take_control(**self.creds(ws, tok_b, "codex-b2"))
        self.svc.click(**self.creds(ws, tok_b, "codex-b2"), x=5, y=5)
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), x=5, y=5)
        self.assertEqual(ctx.exception.code, "busy")

    def test_owner_reattach_after_mcp_restart(self):
        ws, tok, _ = self.acquire()
        new = self.svc.attach(workspace_id=ws, agent_id="claude-a1")["token"]
        self.svc.move_pointer(**self.creds(ws, new), x=10, y=10)
        with self.assertRaises(GuiError):
            self.svc.move_pointer(**self.creds(ws, tok), x=10, y=10)  # superseded token


class InputTests(ServiceTestCase):
    def test_action_id_replays_without_repeating_side_effects(self):
        ws, tok, _ = self.acquire()
        r1 = self.svc.click(**self.creds(ws, tok), x=20, y=30, action_id="a1")
        r2 = self.svc.click(**self.creds(ws, tok), x=20, y=30, action_id="a1")
        self.assertTrue(r2.get("replayed"))
        self.assertEqual(r1["pointer"], r2["pointer"])
        self.assertEqual(len([o for o in self.rt.ops if o[1] == "click"]), 1)

    def test_out_of_bounds_and_bad_input(self):
        ws, tok, _ = self.acquire()
        for kwargs in ({"x": -1, "y": 3}, {"x": 5000, "y": 3}):
            with self.assertRaises(GuiError) as ctx:
                self.svc.click(**self.creds(ws, tok), **kwargs)
            self.assertEqual(ctx.exception.code, "invalid")
        with self.assertRaises(GuiError):
            self.svc.key(**self.creds(ws, tok), keys="hyper+q")
        with self.assertRaises(GuiError):
            self.svc.type_text(**self.creds(ws, tok), text="x" * (aw.MAX_TEXT + 1))

    def test_typed_text_is_never_audited(self):
        ws, tok, _ = self.acquire()
        self.svc.type_text(**self.creds(ws, tok), text="hunter2-secret")
        audit = (self.root / "state" / "audit.jsonl").read_text()
        self.assertNotIn("hunter2", audit)
        self.assertIn("14 chars", audit)

    def test_pause_blocks_input_and_only_user_resumes_user_pause(self):
        ws, tok, _ = self.acquire()
        self.svc.set_paused(workspace_id=ws, paused=True)  # user
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), x=1, y=1)
        self.assertEqual(ctx.exception.code, "paused")
        with self.assertRaises(GuiError):
            self.svc.set_paused(workspace_id=ws, paused=False, actor="agent", **{k: v for k, v in
                                self.creds(ws, tok).items() if k != "workspace_id"})
        self.svc.screenshot(**self.creds(ws, tok))  # observation stays allowed while paused
        self.svc.set_paused(workspace_id=ws, paused=False)
        self.svc.click(**self.creds(ws, tok), x=1, y=1)

    def test_cancel_interrupts_long_typing(self):
        ws, tok, _ = self.acquire()
        seen = []

        def on_call(op, args):
            if op == "type":
                seen.append(1)
                if len(seen) == 2:
                    self.svc._cancel_event(ws).set()
        self.rt.on_call = on_call
        with self.assertRaises(GuiError) as ctx:
            self.svc.type_text(**self.creds(ws, tok), text="a" * 640)
        self.assertEqual(ctx.exception.code, "cancelled")
        self.assertEqual(len(seen), 2)

    def test_browser_click_by_text_and_obscured_fails_closed(self):
        ws, tok, _ = self.acquire(kind="browser", key="b1")
        out = self.svc.click(**self.creds(ws, tok), text="Go")
        self.assertEqual(out["pointer"], [100, 50])
        self.rt.page_value = {"found": True, "x": 3, "y": 3, "obscured": True}
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), text="Go")
        self.assertEqual(ctx.exception.code, "obscured")
        self.rt.page_value = {"found": False}
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), selector="#nope")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_launch_is_desktop_only(self):
        ws, tok, _ = self.acquire(kind="browser", key="b1")
        with self.assertRaises(GuiError):
            self.svc.launch(**self.creds(ws, tok), argv=["true"])


class RecoveryTests(ServiceTestCase):
    def test_crash_detected_then_same_key_recovers_same_workspace(self):
        ws, tok, _ = self.acquire()
        self.rt.displays[ws] = False
        self.svc.tick()
        self.assertEqual(self.svc.workspaces[ws]["state"], "crashed")
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**self.creds(ws, tok), x=1, y=1)
        self.assertEqual(ctx.exception.code, "crashed")
        ws2, tok2, r = self.acquire()
        self.assertEqual((ws2, r["result"], r["workspace"]["recovered"]), (ws, "recovered", 1))
        self.svc.click(**self.creds(ws, tok2), x=1, y=1)

    def test_owner_exit_releases_after_grace(self):
        ws, _, _ = self.acquire(owner_pid=999999)  # not a live pid
        self.svc.tick()
        self.assertEqual(self.svc.workspaces[ws]["state"], "ready")
        self.clock.t += float(self.svc.cfg["owner_grace_seconds"]) + 1
        self.svc.tick()
        self.assertEqual(self.svc.workspaces[ws]["state"], "released")
        self.assertIn(ws, self.rt.swept)

    def test_idle_release_without_owner_pid(self):
        ws, _, _ = self.acquire()
        self.clock.t += float(self.svc.cfg["idle_release_minutes"]) * 60 + 1
        self.svc.tick()
        self.assertEqual(self.svc.workspaces[ws]["state"], "released")

    def test_reconcile_adopts_live_and_marks_dead_lost(self):
        live, _, _ = self.acquire(key="live")
        dead, _, _ = self.acquire(key="dead")
        self.rt.displays[dead] = False
        fresh = AgentWorkspaceService(self.rt, clock=self.clock)  # service restart / reboot
        out = fresh.reconcile()
        self.assertEqual((out["adopted"], out["lost"]), ([live], [dead]))
        _, _, r = fresh.acquire(agent_id="claude-a1", request_key="dead"), None, None
        self.assertEqual(fresh.workspaces[dead]["state"], "ready")
        self.assertEqual(fresh.workspaces[dead]["recovered"], 1)


class OverlayTests(ServiceTestCase):
    def test_overlay_reports_pointer_clicks_activity_and_hidden(self):
        ws, tok, _ = self.acquire()
        self.svc.click(**self.creds(ws, tok), x=40, y=60)
        data = json.loads(aw.overlay_path().read_text())
        agent = data["agents"][0]
        self.assertEqual((agent["x"], agent["y"], agent["clickSeq"], agent["active"]), (40, 60, 1, True))
        self.assertTrue(agent["color"].startswith("#"))
        self.clock.t += float(self.svc.cfg["cursor_idle_seconds"]) + 1
        self.svc.tick()
        self.assertFalse(json.loads(aw.overlay_path().read_text())["agents"][0]["active"])
        self.svc.user_hide_agent("claude-a1", True)
        self.assertTrue(json.loads(aw.overlay_path().read_text())["agents"][0]["hidden"])

    def test_preview_refreshes_only_while_active(self):
        ws, tok, _ = self.acquire()
        self.clock.t += 0.3
        self.svc.tick()
        captures = len([o for o in self.rt.ops if o[1] == "capture"])
        self.assertGreaterEqual(captures, 1)
        self.clock.t += 60
        for _ in range(5):
            self.clock.t += 2
            self.svc.tick()
        self.assertEqual(len([o for o in self.rt.ops if o[1] == "capture"]), captures)


class ConcurrencyTests(ServiceTestCase):
    def test_two_agents_act_in_parallel_on_separate_workspaces(self):
        a, ta, _ = self.acquire(agent="claude-a1")
        b, tb, _ = self.acquire(agent="codex-b2")
        errors = []

        def work(ws, tok, agent):
            try:
                for i in range(25):
                    self.svc.click(workspace_id=ws, agent_id=agent, token=tok, x=i, y=i)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
        threads = [threading.Thread(target=work, args=a_) for a_ in ((a, ta, "claude-a1"), (b, tb, "codex-b2"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        per_ws = {w: len([o for o in self.rt.ops if o[0] == w and o[1] == "click"]) for w in (a, b)}
        self.assertEqual(per_ws, {a: 25, b: 25})


class McpToolTests(unittest.TestCase):
    def test_gui_tools_registered_without_legacy_aliases(self):
        import loom_mcp
        names = [t["name"] for t in loom_mcp.tool_list()]
        self.assertIn("loom_gui_acquire", names)
        self.assertIn("loom_gui_screenshot", names)
        self.assertNotIn("tabby_gui_acquire", loom_mcp.TOOL_INDEX)
        for name in names:
            self.assertFalse(name.startswith("tabby"))

    def test_remote_transport_fails_closed(self):
        import loom_mcp
        from tabby import gui_tools
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"LOOM_AGENT_INPUT_CONFIG": str(Path(tmp) / "c.json")}), \
                patch.object(gui_tools, "TRANSPORT", "http"), \
                patch.object(gui_tools, "request", side_effect=AssertionError("must not reach the service")):
            out = loom_mcp.call_tool("loom_gui_acquire", {"request_key": "x"})
        self.assertTrue(out["isError"])
        self.assertIn("remote MCP", out["content"][0]["text"])

    def test_screenshot_is_returned_as_image_content(self):
        import loom_mcp
        from tabby import gui_tools
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "s.png"
            shot.write_bytes(b"\x89PNG fake")
            replies = iter([{"ok": True, "token": "t"},
                            {"ok": True, "path": str(shot), "width": 1, "height": 1}])
            with patch.object(gui_tools, "request", side_effect=lambda *a, **k: next(replies)), \
                    patch.object(gui_tools, "_tokens", {}):
                out = loom_mcp.call_tool("loom_gui_screenshot", {"workspace_id": "gws-1"})
        self.assertFalse(out["isError"])
        self.assertEqual(out["content"][1]["type"], "image")
        self.assertNotIn("_mcp_image", out["structuredContent"])


class KeyParsingTests(unittest.TestCase):
    def test_parse_combo(self):
        from tabby.xinput import parse_combo, char_keysym
        self.assertEqual(parse_combo("ctrl+shift+T"), (["Control_L", "Shift_L"], "t"))
        self.assertEqual(parse_combo("Enter"), ([], "Return"))
        self.assertEqual(parse_combo("alt+F4"), (["Alt_L"], "F4"))
        self.assertEqual(parse_combo("ctrl++"), (["Control_L"], "plus"))
        with self.assertRaises(ValueError):
            parse_combo("hyper+x")
        self.assertEqual(char_keysym("a"), ord("a"))
        self.assertEqual(char_keysym("\n"), 0xFF0D)
        self.assertEqual(char_keysym("✓"), 0x01002713)

    def test_xauthority_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "xa"
            aw.write_xauthority(p, b"\x01" * 16)
            data = p.read_bytes()
            self.assertEqual(data[:2], b"\xff\xff")
            self.assertIn(b"MIT-MAGIC-COOKIE-1", data)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()


class HumanPathTests(unittest.TestCase):
    def test_path_is_curved_ends_exactly_and_scales_with_distance(self):
        import math
        import random
        pts, ms = aw.human_path(100, 100, 1100, 700, random.Random(7))
        self.assertEqual(pts[-1], (1100, 700))
        self.assertGreater(len(pts), 20)
        # not a straight line: some point leaves the chord by a few pixels
        def off(p):
            return abs((700 - 100) * p[0] - (1100 - 100) * p[1] + 1100 * 100 - 700 * 100) / math.hypot(600, 1000)
        self.assertGreater(max(off(p) for p in pts), 5)
        short_ms = aw.human_path(100, 100, 130, 110, random.Random(7))[1]
        self.assertLess(short_ms, ms)
        self.assertEqual(aw.human_path(5, 5, 5, 5)[0], [(5, 5)])
        # never jumps: consecutive samples stay close
        steps = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
        self.assertLess(max(steps), 80)


class NestedBackendUnitTests(ServiceTestCase):
    def test_nested_config_is_minimal_scale_one_and_agent_colored(self):
        rt = aw.ProcessRuntime()
        with tempfile.TemporaryDirectory() as tmp:
            path = rt.nested_config({"id": "gws-x"}, {"root": Path(tmp)}, "#A8E6CF")
            text = path.read_text()
        self.assertIn('scale = 1', text)
        self.assertIn("rgba(A8E6CFff)", text)
        self.assertIn("animations = { enabled = false }", text)

    def test_falls_back_to_xvfb_when_nested_is_unavailable(self):
        self.rt.nested_available = lambda: (False, "no host Wayland compositor")
        ws, _, r = self.acquire()
        self.assertEqual(r["workspace"]["backend"], "xvfb")

    def test_nested_backend_used_and_size_follows_the_host_window(self):
        started = []
        self.rt.nested_available = lambda: (True, "")

        def start_nested(ws, color):
            started.append(color)
            self.rt.displays[ws["id"]] = True
            return {"display": "wayland-1", "xvfb_pid": 1}
        self.rt.start_nested = start_nested
        ws, tok, r = self.acquire()
        self.assertEqual((r["workspace"]["backend"], len(started)), ("nested", 1))
        helper = self.svc._helpers[ws]
        orig = helper.call

        def call(op, timeout=10.0, **args):
            if op == "hello":
                return {"ok": True, "width": 1920, "height": 1200, "pointer": [0, 0]}
            return orig(op, timeout, **args)
        helper.call = call
        self.svc.click(**self.creds(ws, tok), x=1900, y=1100)  # valid only at the new size
        self.assertEqual((self.svc.workspaces[ws]["width"], self.svc.workspaces[ws]["height"]), (1920, 1200))
