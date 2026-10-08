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
                         "loom_mission_health", "loom_idea_dispatch"}.issubset(names))

    def test_dispatch_idea_is_explicit_and_does_not_spawn(self):
        with patch.object(missions, "request", return_value={"ok": True, "idea": {"id": "a"*24}}) as send:
            response = loom_mcp.call_tool("loom_idea_dispatch", {"idea_id": "a"*24, "mission_id": "261008-wlnf"})
            self.assertFalse(response["isError"])
            self.assertEqual(send.call_args.args[0], {
                "action": "dispatch", "idea_id": "a"*24, "mission_id": "261008-wlnf"})
        with patch.object(missions, "request") as send:
            with self.assertRaises(missions.MissionBridgeError):
                missions.idea_dispatch(idea_id="wrong; touch /tmp/unsafe", mission_id="261008-wlnf")
            send.assert_not_called()

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

    def test_handoff_remote_is_fixed_and_json_only(self):
        received={}
        class Result:
            stdout='{"ok":true,"registration":{"mission":"261008-abcd"}}'
            returncode=0
        def runner(cmd,**kw):
            received.update(command=cmd,**kw)
            return Result()
        out=missions._handoff_ssh(missions.HANDOFF_REGISTER_COMMAND,
                                 {"mission":"261008-abcd","origin_ref":"a; rm -rf /"},runner=runner)
        self.assertTrue(out["ok"])
        self.assertEqual(received["command"][-2:],["philipedia",missions.HANDOFF_REGISTER_COMMAND])
        self.assertNotIn("rm -rf"," ".join(received["command"]))
        self.assertEqual(json.loads(received["input"])["origin_ref"],"a; rm -rf /")

    def test_mission_creation_attaches_origin(self):
        with patch.object(missions,"mission_health",return_value={"sandbox_available":True}), \
             patch.object(missions,"request",return_value={"ok":True,"task":{"id":"261008-abcd"}}) as send, \
             patch.object(missions,"handoff_register",return_value={"ok":True}) as register:
            out=missions.mission_create(goal="Implement",repo="/home/qwqc/loom",
                                        origin_ref="origin-123",origin_source="chatgpt",
                                        auto_continuation=True)
        self.assertIn("handoff",out)
        self.assertEqual(send.call_args.args[0]["action"],"create")
        self.assertEqual(register.call_args.kwargs["origin_ref"],"origin-123")
        self.assertTrue(register.call_args.kwargs["auto_continuation"])

    def test_failed_origin_registration_does_not_lie_about_created_mission(self):
        with patch.object(missions,"mission_health",return_value={"sandbox_available":True}), \
             patch.object(missions,"request",return_value={"ok":True,"task":{"id":"261008-abcd"}}), \
             patch.object(missions,"handoff_register",side_effect=missions.MissionBridgeError("not connected")):
            out=missions.mission_create(goal="Implement",repo="/home/qwqc/loom")
        self.assertEqual(out["task"]["id"],"261008-abcd")
        self.assertIn("not connected",out["handoff_error"])

    def test_mcp_handoff_tools_are_visible(self):
        names={x["name"] for x in loom_mcp.tool_list()}
        self.assertIn("loom_handoff_register",names)
        self.assertIn("loom_handoff_status",names)
        with patch.object(missions,"handoff_status",return_value={"origins":3,"pending":0}):
            response=loom_mcp.call_tool("loom_handoff_status",{})
        self.assertEqual(response["structuredContent"]["origins"],3)

    def test_mcp_error_is_structured(self):
        with patch.object(missions, "mission_list", side_effect=missions.MissionBridgeError("offline")):
            response = loom_mcp.call_tool("loom_mission_list", {})
        self.assertTrue(response["isError"])
        self.assertIn("offline", response["structuredContent"]["error"])


if __name__ == "__main__":
    unittest.main()

