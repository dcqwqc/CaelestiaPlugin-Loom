"""Real-process integration tests: Xvfb, the X input broker and Firefox.

Skipped automatically where Xvfb/Firefox are missing. They never touch the
user's session: every display is a private Xvfb and the runtime directory is
a throwaway one.
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tabby import agent_workspaces as aw
from tabby.agent_workspaces import AgentWorkspaceService, GuiError

HAVE_XVFB = bool(shutil.which("Xvfb"))
HAVE_FIREFOX = bool(shutil.which("firefox"))
PAGE = ("data:text/html,<title>start</title><input id=q placeholder=Search>"
        "<button onclick=\"document.title=document.getElementById('q').value\">Go</button>")


def hypr_clients():
    if not shutil.which("hyprctl") or not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return None
    out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=3).stdout
    return sorted(c.get("address") for c in json.loads(out or "[]"))


def tagged_pids(ws_id):
    tag = f"LOOM_AGENT_WORKSPACE={ws_id}".encode()
    out = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if tag in (entry / "environ").read_bytes().split(b"\0"):
                    out.append(int(entry.name))
            except OSError:
                pass
    return out


@unittest.skipUnless(HAVE_XVFB, "Xvfb not installed")
class IntegrationBase(unittest.TestCase):
    def setUp(self):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        # Short path: broker sockets must fit AF_UNIX's 108-byte limit.
        self.run_dir = Path(tempfile.mkdtemp(prefix="lai-", dir=runtime))
        self.state = tempfile.TemporaryDirectory()
        env = {"LOOM_AGENT_INPUT_RUNTIME": str(self.run_dir / "ai"),
               "LOOM_AGENT_INPUT_STATE": self.state.name,
               "LOOM_AGENT_INPUT_CONFIG": str(Path(self.state.name) / "cfg.json")}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.svc = AgentWorkspaceService()
        self.created = []

    def tearDown(self):
        for ws_id in self.created:
            try:
                self.svc.release(workspace_id=ws_id, actor="user", keep_profile=False)
            except GuiError:
                pass
            aw.ProcessRuntime().sweep(ws_id, grace=1)
        self.svc.shutdown()
        self.env.stop()
        self.state.cleanup()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def acquire(self, agent, key, kind, **kw):
        r = self.svc.acquire(agent_id=agent, request_key=key, kind=kind, **kw)
        self.created.append(r["workspace"]["id"])
        return {"workspace_id": r["workspace"]["id"], "agent_id": agent, "token": r["token"]}


class DesktopIntegrationTests(IntegrationBase):
    def test_desktop_workspace_input_capture_and_clean_release(self):
        before = hypr_clients()
        a = self.acquire("claude-it1", "d1", "desktop", size="1024x700")
        moved = self.svc.move_pointer(**a, x=300, y=200, duration_ms=100)
        self.assertEqual(moved["pointer"], [300, 200])
        self.svc.key(**a, keys="ctrl+shift+t")
        self.svc.type_text(**a, text="abc ✓")
        shot = self.svc.screenshot(**a, max_width=512)
        self.assertTrue(Path(shot["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(shot["image_width"], 512)
        state = self.svc.interaction_state(**a)
        self.assertEqual(state["pointer"], [300, 200])
        self.assertEqual(hypr_clients(), before)  # nothing appeared in the user's compositor
        ws_id = a["workspace_id"]
        self.assertTrue(tagged_pids(ws_id))
        self.svc.release(**a)
        time.sleep(0.3)
        self.assertEqual(tagged_pids(ws_id), [])
        self.assertFalse((aw.runtime_dir() / ws_id).exists())

    def test_crash_is_detected_and_same_key_recovers(self):
        a = self.acquire("claude-it2", "d2", "desktop")
        ws = self.svc.workspaces[a["workspace_id"]]
        os.kill(int(ws["xvfb_pid"]), signal.SIGKILL)
        time.sleep(0.3)
        self.svc.tick()
        self.assertEqual(ws["state"], "crashed")
        with self.assertRaises(GuiError) as ctx:
            self.svc.click(**a, x=1, y=1)
        self.assertEqual(ctx.exception.code, "crashed")
        b = self.acquire("claude-it2", "d2", "desktop")
        self.assertEqual(b["workspace_id"], a["workspace_id"])
        self.assertEqual(self.svc.workspaces[b["workspace_id"]]["recovered"], 1)
        self.svc.click(**b, x=10, y=10)

    def test_two_workspaces_are_isolated_from_each_other(self):
        a = self.acquire("claude-it3", "iso", "desktop", size="800x600")
        b = self.acquire("codex-it3", "iso", "desktop", size="800x600")
        self.assertNotEqual(self.svc.workspaces[a["workspace_id"]]["display"],
                            self.svc.workspaces[b["workspace_id"]]["display"])
        self.svc.move_pointer(**a, x=100, y=100, duration_ms=0)
        self.svc.move_pointer(**b, x=700, y=500, duration_ms=0)
        self.assertEqual(self.svc.interaction_state(**a)["pointer"], [100, 100])
        self.assertEqual(self.svc.interaction_state(**b)["pointer"], [700, 500])
        with self.assertRaises(GuiError):  # b's token cannot drive a
            self.svc.click(workspace_id=a["workspace_id"], agent_id=b["agent_id"], token=b["token"], x=1, y=1)


@unittest.skipUnless(HAVE_XVFB and HAVE_FIREFOX, "Xvfb/Firefox not installed")
class BrowserIntegrationTests(IntegrationBase):
    def test_browser_selector_actions_survive_service_restart(self):
        a = self.acquire("claude-it4", "b1", "browser", url=PAGE)
        self.svc.type_text(**a, selector="#q", text="Grüße 42")
        out = self.svc.click(**a, text="Go")
        self.assertFalse(out["target"]["obscured"])
        self.assertEqual(self.svc.interaction_state(**a)["page"]["title"], "Grüße 42")
        # Simulate a service restart: a new instance must adopt the live broker,
        # which still owns the browser's BiDi session.
        self.svc.shutdown()
        self.svc = AgentWorkspaceService()
        self.assertEqual(self.svc.reconcile()["adopted"], [a["workspace_id"]])
        self.svc.type_text(**a, selector="#q", text="!")
        self.svc.click(**a, text="Go")
        self.assertEqual(self.svc.interaction_state(**a)["page"]["title"], "Grüße 42!")

    def test_browser_crash_recovers_with_profile(self):
        a = self.acquire("claude-it5", "b2", "browser", url="about:blank")
        ws = self.svc.workspaces[a["workspace_id"]]
        os.killpg(int(ws["browser_pid"]), signal.SIGKILL)
        time.sleep(0.5)
        self.svc.tick()
        self.assertEqual(ws["state"], "crashed")
        b = self.acquire("claude-it5", "b2", "browser", url=PAGE)
        self.assertEqual(self.svc.interaction_state(**b)["page"]["title"], "start")


if __name__ == "__main__":
    unittest.main()
