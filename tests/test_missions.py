import base64
import json
import subprocess
import unittest
from unittest.mock import patch
from tabby import missions
import loom_mcp


class FakeProc:
    stdout = '{"ok":true,"tasks":[]}'
    returncode = 0
    stderr = ""


class MissionBridgeTests(unittest.TestCase):
    def test_fixed_ssh_command_and_encoded_goal(self):
        received = {}
        def runner(cmd, **kw):
            received.update(cmd=cmd, **kw)
            return FakeProc()
        result = missions.request({"action": "create", "goal": "echo 'x'; rm -rf /", "repo": "/home/qwqc/loom"}, runner=runner)
        self.assertTrue(result["ok"])
        self.assertEqual(received["cmd"][-2:], ["philipedia", missions.BRIDGE_COMMAND])
        raw = json.loads(base64.b64decode(received["input"].strip()).decode())
        self.assertEqual(raw["goal"], "echo 'x'; rm -rf /")
        self.assertNotIn("rm -rf", " ".join(received["cmd"]))

    def test_invalid_paths_agents_and_ids_fail_before_ssh(self):
        with self.assertRaises(missions.MissionBridgeError):
            missions.mission_create(goal="x", repo="/etc", agent="codex")
        with self.assertRaises(missions.MissionBridgeError):
            missions.mission_create(goal="x", repo="/home/qwqc/foo", agent="unknown")
        with self.assertRaises(missions.MissionBridgeError):
            missions.mission_create(goal="x", repo="/home/qwqc/foo/../bar")
        with self.assertRaises(missions.MissionBridgeError):
            missions.mission_activity("x; touch /tmp/test")

    def test_no_shell_transport_action(self):
        with self.assertRaises(missions.MissionBridgeError):
            missions.request({"action": "shell", "command": "whoami"})

    def test_invalid_remote_response_is_error(self):
        class Bad:
            stdout = "not json"
            returncode = 1
        with self.assertRaisesRegex(missions.MissionBridgeError,"invalid JSON"):
            missions.request({"action": "status"}, runner=lambda *a, **k: Bad())

    def test_timeouts_fail_closed(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired("ssh", 18)
        with self.assertRaisesRegex(missions.MissionBridgeError, "connection failed"):
            missions.request({"action": "status"}, runner=runner)

    def test_mcp_names_are_exposed(self):
        names = {x["name"] for x in loom_mcp.tool_list()}
        self.assertTrue({"loom_mission_list", "loom_mission_activity", "loom_mission_create",
                         "loom_mission_resume", "loom_idea_capture", "loom_idea_list",
                         "loom_mission_health"}.issubset(names))

    def test_capture_idea_is_separate_from_execution(self):
        with patch.object(missions, "request", return_value={"ok": True, "ideas": [{"id": "abc"}]}) as send:
            result = loom_mcp.call_tool("loom_idea_capture", {"request_id": "voice-1",
                           "ideas": [{"title": "Fix mute", "body": "The button no longer mutes"}]})
        self.assertFalse(result["isError"])
        action = send.call_args.args[0]
        self.assertEqual(action["action"], "capture")
        self.assertEqual(action["request_id"], "voice-1")

    def test_broken_remote_sandbox_blocks_work_without_losing_idea(self):
        with patch.object(missions, "mission_health", return_value={"sandbox_available": False}), \
             patch.object(missions, "request") as send:
            with self.assertRaisesRegex(missions.MissionBridgeError, "unavailable"):
                missions.mission_create(goal="Fix mute", repo="/home/qwqc/loom")
            send.assert_not_called()

    def test_mcp_error_is_structured(self):
        with patch.object(missions, "mission_list", side_effect=missions.MissionBridgeError("offline")):
            response = loom_mcp.call_tool("loom_mission_list", {})
        self.assertTrue(response["isError"])
        self.assertIn("offline", response["structuredContent"]["error"])


if __name__ == "__main__":
    unittest.main()

