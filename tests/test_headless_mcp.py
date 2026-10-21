import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import loom_mcp
from tabby import desktop_adapter, missions


class Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.body


class HeadlessRuntimeTests(unittest.TestCase):
    def test_adapter_uses_fixed_authenticated_mcp_tool_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text("x" * 40)
            token_path.chmod(0o600)
            seen = {}
            def opener(request, timeout):
                seen.update(url=request.full_url, auth=request.headers["Authorization"],
                            body=json.loads(request.data), timeout=timeout)
                return Response(json.dumps({"result": {"structuredContent": {"ok": True}}}).encode())
            result = desktop_adapter.call({"command": "work-list"}, opener=opener, token_path=token_path)
        self.assertTrue(result["ok"])
        self.assertEqual(seen["url"], desktop_adapter.MIRAI_MCP_URL)
        self.assertEqual(seen["auth"], "Bearer " + "x" * 40)
        self.assertEqual(seen["body"]["params"]["name"], "loom_task_list")

    def test_adapter_rejects_unknown_command_before_network(self):
        with self.assertRaisesRegex(desktop_adapter.DesktopUnavailable, "allowlisted"):
            desktop_adapter.call({"command": "shell", "argv": ["id"]}, opener=lambda *a, **k: self.fail())

    def test_headless_mission_survives_mirai_outage_and_desktop_is_clear_error(self):
        with patch.dict(os.environ, {"LOOM_MCP_ROLE": "philipedia"}), \
             patch.object(missions, "mission_list", return_value={"ok": True, "tasks": [{"id": "m1"}]}), \
             patch.object(desktop_adapter, "call", side_effect=desktop_adapter.DesktopUnavailable("Mirai desktop is unavailable")):
            headless = loom_mcp.call_tool("loom_mission_list", {})
            desktop = loom_mcp.call_tool("loom_show", {})
        self.assertFalse(headless["isError"])
        self.assertEqual(headless["structuredContent"]["tasks"][0]["id"], "m1")
        self.assertTrue(desktop["isError"])
        self.assertEqual(desktop["structuredContent"]["error"], "Mirai desktop is unavailable")

    def test_philipedia_catalog_preserves_all_mirai_tool_names(self):
        with patch.dict(os.environ, {"LOOM_MCP_ROLE": "philipedia"}):
            names = {tool["name"] for tool in loom_mcp.tool_list()}
        self.assertEqual(len(names), 93)
        self.assertTrue({
            "loom_gui_acquire", "loom_notify", "loom_task_create",
            "loom_mission_health", "loom_project_links", "loom_project_link",
            "loom_runtime_health",
        }.issubset(names))

    def test_mirai_only_extra_tool_fails_closed_without_impacting_core(self):
        with patch.dict(os.environ, {"LOOM_MCP_ROLE": "philipedia"}), \
             patch.object(desktop_adapter, "call_named_tool",
                          side_effect=desktop_adapter.DesktopUnavailable("Mirai desktop is unavailable")), \
             patch.object(missions, "project_links", return_value={"ok": True, "projects": []}):
            desktop = loom_mcp.call_tool("loom_gui_acquire", {"request_key": "test"})
            headless = loom_mcp.call_tool("loom_project_links", {})
        self.assertTrue(desktop["isError"])
        self.assertTrue(desktop["structuredContent"]["desktop_unavailable"])
        self.assertFalse(headless["isError"])

    def test_health_separates_philipedia_and_optional_mirai(self):
        with patch.dict(os.environ, {"LOOM_MCP_ROLE": "philipedia"}), \
             patch.object(desktop_adapter, "health", return_value={"configured": True, "available": False, "reason": "offline"}):
            health = loom_mcp.runtime_health()
        self.assertTrue(health["philipedia"]["headless_tools"])
        self.assertFalse(health["mirai"]["available"])


if __name__ == "__main__":
    unittest.main()
