"""Regression: remote MCP must never mistake Loom's Voice chat for its caller."""
import unittest
from unittest.mock import patch

import loom_mcp


class ChatOriginRouteTests(unittest.TestCase):
    def test_missing_url_fails_closed_without_ipc(self):
        with patch.object(loom_mcp, "_ipc") as ipc, self.assertRaisesRegex(loom_mcp.ToolError, "Cannot identify"):
            loom_mcp.t_chat_route({"lifecycle": "working"})
        ipc.assert_not_called()

    def test_ambiguous_current_voice_alias_fails_closed(self):
        with patch.object(loom_mcp, "_ipc") as ipc, self.assertRaisesRegex(loom_mcp.ToolError, "current=true"):
            loom_mcp.t_chat_route({"current": True, "lifecycle": "working"})
        ipc.assert_not_called()

    def test_malformed_or_wrong_origin_never_routes(self):
        for url in ("https://example.com/c/known", "https://chatgpt.com/g/g-my-gpt", "https://chatgpt.com/"):
            with self.subTest(url=url), patch.object(loom_mcp, "_ipc") as ipc, self.assertRaises(loom_mcp.ToolError):
                loom_mcp.t_chat_route({"url": url, "lifecycle": "working"})
            ipc.assert_not_called()

    def test_exact_url_is_forwarded_without_current_fallback(self):
        url = "https://chatgpt.com/c/01234567-89ab-4cde-8fab-0123456789ab"
        with patch.object(loom_mcp, "_ipc", return_value={"ok": True}) as ipc:
            self.assertTrue(loom_mcp.t_chat_route({"url": url, "lifecycle": "working"})["ok"])
        payload = ipc.call_args.args[0]
        self.assertEqual(payload["url"], url)
        self.assertEqual(payload["lifecycle"], "working")
        self.assertNotIn("current", payload)

    def test_tool_schema_excludes_current_and_requires_url(self):
        tools = {t[0]: t for t in loom_mcp.TOOLS}
        schema = tools["loom_chat_route"][2]
        self.assertIn("url", schema["required"])
        self.assertNotIn("current", schema["properties"])
        self.assertIn("loom_voice_chat_route", tools)


if __name__ == "__main__":
    unittest.main()
