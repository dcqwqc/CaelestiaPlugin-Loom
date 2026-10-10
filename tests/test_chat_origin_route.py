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

    def test_exact_local_origin_fingerprint_resolves_then_routes(self):
        origin_url = "https://chatgpt.com/c/01234567-89ab-4cde-8fab-0123456789ab"
        messages = ["Please fix the automatic project routing without moving another chat."]
        with patch.object(loom_mcp, "_ipc", side_effect=[
            {"ok": True, "url": origin_url, "result": "origin-verified-local-zen"},
            {"ok": True, "result": "routed-working", "url": origin_url},
        ]) as ipc:
            result = loom_mcp.t_chat_route({"origin_messages": messages, "lifecycle": "working"})
        self.assertTrue(result["ok"])
        self.assertEqual(ipc.call_count, 2)
        self.assertEqual(ipc.call_args_list[0].args[0]["command"], "chat-origin-resolve")
        self.assertEqual(ipc.call_args_list[1].args[0]["url"], origin_url)

    def test_unsafe_fingerprints_fail_without_routing(self):
        bad = [[], ["do all"], ["repeated", "repeated"], [" " * 40], [5], ["A" * 2401]]
        for fingerprint in bad:
            with self.subTest(fingerprint=fingerprint), patch.object(loom_mcp, "_ipc") as ipc, self.assertRaises(loom_mcp.ToolError):
                loom_mcp.t_chat_route({"origin_messages": fingerprint, "lifecycle": "working"})
            ipc.assert_not_called()

    def test_url_and_origin_messages_are_mutually_exclusive(self):
        with patch.object(loom_mcp, "_ipc") as ipc, self.assertRaisesRegex(loom_mcp.ToolError, "either"):
            loom_mcp.t_chat_route({"url": "https://chatgpt.com/c/abc", "origin_messages": ["A long user message for reliable identity"], "lifecycle": "working"})
        ipc.assert_not_called()

    def test_origin_discovery_error_never_routes(self):
        with patch.object(loom_mcp, "_ipc", side_effect=loom_mcp.ToolError("origin-ambiguous")) as ipc, self.assertRaisesRegex(loom_mcp.ToolError,"origin-ambiguous"):
            loom_mcp.t_chat_route({"origin_messages": ["A sufficiently long user message for routing"], "lifecycle": "working"})
        self.assertEqual(ipc.call_count, 1)

    def test_unreloaded_bridge_rejects_origin_discovery_without_browser_io(self):
        from tabby.zen import ZenClient
        z = ZenClient.__new__(ZenClient)
        z._read = lambda: {"version": "0.11.0", "bridgeLoaded": True}
        with patch.object(z, "call") as call:
            result = z.resolve_chat_origin(["This identifies a real external ChatGPT conversation"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], "origin-bridge-reload-required")
        call.assert_not_called()

    def test_new_bridge_dispatches_read_only_fingerprint(self):
        from tabby.zen import ZenClient
        z = ZenClient.__new__(ZenClient)
        z._read = lambda: {"version": "0.12.0", "bridgeLoaded": True}
        with patch.object(z, "call", return_value={"ok": True}) as call:
            result = z.resolve_chat_origin(["This identifies a real external ChatGPT conversation"])
        self.assertTrue(result["ok"])
        self.assertEqual(call.call_args.args[0], "chat-origin-resolve")

    def test_tool_schema_excludes_current_and_requires_url(self):
        tools = {t[0]: t for t in loom_mcp.TOOLS}
        schema = tools["loom_chat_route"][2]
        self.assertIn("lifecycle", schema["required"])
        self.assertNotIn("url", schema["required"])
        self.assertIn("origin_messages", schema["properties"])
        self.assertNotIn("current", schema["properties"])
        self.assertIn("loom_voice_chat_route", tools)


if __name__ == "__main__":
    unittest.main()
