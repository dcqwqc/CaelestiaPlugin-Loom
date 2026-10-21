import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import loom_mcp
from tabby import fastpath, missions, desktop_adapter


def ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout)


class FastPathTests(unittest.TestCase):
    def test_known_apps_bilingual(self):
        for text in ("Open Spotify", "öffne spotify", "LAUNCH app spotify", "Start the Spotify"):
            self.assertEqual(fastpath.resolve(text), {"kind": "app", "target": "spotify"})

    def test_arbitrary_shell_and_unknown_apps_never_dispatch(self):
        for text in ("open rm -rf /", "execute bash", "open firefox; rm /tmp/x",
                     "open my passwords", "play music", ""):
            self.assertNotIn("kind", fastpath.resolve(text))

    def test_exact_sumi_uuid(self):
        text = "open sumi://page/877329d2-649e-4bec-b40b-ee6c955d7d65"
        self.assertEqual(fastpath.resolve(text)["target"], "877329d2-649e-4bec-b40b-ee6c955d7d65")
        self.assertNotIn("kind", fastpath.resolve("open sumi://page/not-an-id"))

    def test_missing_compositor_signature_resolves_one_safe_instance(self):
        calls = []
        def mock_run(argv, **kw):
            calls.append((argv, kw))
            if argv[-1] == "instances":
                return ok(json.dumps([{"instance": "verified_signature", "wl_socket": "wayland-1"}]))
            return ok("[]")
        with patch.dict(fastpath.os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "",
                                              "WAYLAND_DISPLAY": ""}), \
             patch.object(fastpath.subprocess, "run", side_effect=mock_run):
            fastpath._run(["hyprctl", "-j", "clients"])
        self.assertEqual(calls[-1][1]["env"]["HYPRLAND_INSTANCE_SIGNATURE"], "verified_signature")

    def test_ambiguous_session_refuses_launch(self):
        def mock_run(argv, **kw):
            return ok(json.dumps([{"instance": "a", "wl_socket": "wayland-1"},
                                  {"instance": "b", "wl_socket": "wayland-2"}]))
        with patch.dict(fastpath.os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "",
                                              "WAYLAND_DISPLAY": ""}), \
             patch.object(fastpath.subprocess, "run", side_effect=mock_run):
            with self.assertRaisesRegex(OSError, "ambiguous"):
                fastpath._run(["hyprctl", "-j", "clients"])

    def test_existing_window_focused_and_verified(self):
        calls = []
        def runner(argv, timeout=1):
            calls.append(argv)
            if argv[-1] == "clients":
                return ok(json.dumps([{"class": "spotify", "address": "0xabc"}]))
            if argv[-1] == "activewindow":
                return ok('{"class":"spotify"}')
            return ok()
        result = fastpath.execute({"kind": "app", "target": "spotify"}, run=runner, sleep=lambda _: None)
        self.assertEqual(result["state"], "verified")
        self.assertIn(["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xabc" })'], calls)
        self.assertFalse(any("gio" in command for command in calls))

    def test_new_app_launches_registered_desktop_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "spotify.desktop"
            desktop.write_text("[Desktop Entry]\nType=Application\nName=Spotify\nExec=spotify\n")
            calls = []
            def runner(argv, timeout=1):
                calls.append(argv)
                if argv[-1] == "clients":
                    return ok("[]")
                if argv[-1] == "activewindow":
                    return ok('{"class":"spotify"}')
                return ok()
            result = fastpath.execute({"kind": "app", "target": "spotify"}, run=runner,
                                      paths=(Path(tmp),), sleep=lambda _: None)
            self.assertEqual(result["state"], "verified")
            self.assertIn(["gio", "launch", str(desktop)], calls)

    def test_missing_app_fails_without_invoking_launcher(self):
        calls = []
        def runner(argv, timeout=1):
            calls.append(argv)
            return ok("[]")
        with tempfile.TemporaryDirectory() as tmp:
            result = fastpath.execute({"kind": "app", "target": "spotify"}, run=runner,
                                      paths=(Path(tmp),))
        self.assertEqual(result["state"], "not_found")
        self.assertFalse(any(c[0] == "gio" for c in calls))

    def test_uri_dispatch_is_not_false_verified(self):
        calls = []
        def runner(argv, timeout=1):
            calls.append(argv)
            return ok()
        action = fastpath.resolve("open sumi://page/877329d2-649e-4bec-b40b-ee6c955d7d65")
        result = fastpath.execute(action, run=runner)
        self.assertEqual(result["state"], "dispatched_unverified")
        self.assertEqual(calls[0], ["gio", "open", "sumi://page/877329d2-649e-4bec-b40b-ee6c955d7d65"])

    def test_replay_is_durable_without_repeating_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "receipts.sqlite3"
            seen = []
            def executor(action):
                seen.append(action)
                return {"state": "verified", "target": action["target"]}
            one = fastpath.FastPath(db, hostname=lambda: "mirai")
            first = one.run("loom:req:001", "Open Spotify", "mirai", executor=executor)
            second = fastpath.FastPath(db, hostname=lambda: "mirai").run(
                "loom:req:001", "Open Spotify", "mirai", executor=executor)
            self.assertEqual(first["state"], "verified")
            self.assertTrue(second["replayed"])
            self.assertEqual(len(seen), 1)
            denied = one.run("loom:req:001", "Open Zen", "mirai", executor=executor)
            self.assertEqual(denied["state"], "denied")

    def test_uncertain_receipt_never_auto_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = fastpath.FastPath(Path(tmp) / "a.db", hostname=lambda: "mirai")
            digest = fastpath.hashlib.sha256(json.dumps((
                {"kind": "app", "target": "spotify"}, "mirai"), sort_keys=True).encode()).hexdigest()
            with closing(sqlite3.connect(router.db_path)) as db, db:
                db.execute("INSERT INTO receipts VALUES (?,?,?,?)",
                           ("loom:req:002", digest, "started", "{}"))
            result = router.run("loom:req:002", "Open Spotify", "mirai",
                                executor=lambda a: self.fail("must not invoke"))
            self.assertEqual(result["state"], "uncertain_outcome")

    def test_no_inferred_philipedia_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = fastpath.FastPath(Path(tmp) / "a.db", hostname=lambda: "philipedia")
            self.assertEqual(router.run("loom:req:003", "Open Spotify", "active")["state"],
                             "device_unavailable")
            self.assertEqual(router.run("loom:req:003", "Open Spotify", "mirai")["state"],
                             "device_unavailable")

    def test_headless_forward_requires_named_device(self):
        with patch.object(missions, "_local_runtime", return_value=True), \
             patch.object(desktop_adapter, "call_named_tool", return_value={
                 "structuredContent": {"state": "verified", "target": "spotify"}}) as forward:
            out = loom_mcp.call_tool("loom_fast_action", {
                "request_id": "loom:req:004", "utterance": "Open Spotify", "device": "active"})
            self.assertEqual(out["structuredContent"]["state"], "device_unavailable")
            forward.assert_not_called()
            out = loom_mcp.call_tool("loom_fast_action", {
                "request_id": "loom:req:004", "utterance": "Open Spotify", "device": "mirai"})
            self.assertEqual(out["structuredContent"]["state"], "verified")
            self.assertEqual(forward.call_args.args[0], "loom_fast_action")
            self.assertEqual(forward.call_args.kwargs["device"], "mirai")
            out = loom_mcp.call_tool("loom_fast_action", {
                "request_id": "loom:req:006", "utterance": "Open Spotify", "device": "tachibana"})
            self.assertEqual(out["structuredContent"]["state"], "verified")
            self.assertEqual(forward.call_args.kwargs["device"], "tachibana")

    def test_headless_offline_fails_closed(self):
        with patch.object(missions, "_local_runtime", return_value=True), \
             patch.object(desktop_adapter, "call_named_tool",
                          side_effect=desktop_adapter.DesktopUnavailable("Mirai offline")):
            out = loom_mcp.call_tool("loom_fast_action", {
                "request_id": "loom:req:005", "utterance": "Open Spotify", "device": "mirai"})
            self.assertEqual(out["structuredContent"]["state"], "device_unavailable")


if __name__ == "__main__":
    unittest.main()
