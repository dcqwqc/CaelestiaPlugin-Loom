import unittest
from unittest.mock import patch

import loom_mcp


class RegisteredOriginRoutingTests(unittest.TestCase):
    def test_registered_mobile_origin_routes_without_browser_fingerprint(self):
        url = "https://chatgpt.com/c/mobile-known"
        with patch.object(loom_mcp, "_ipc", side_effect=[
            {"ok": True, "origin": {"canonical_url": url}},
            {"ok": True, "origin": {"canonical_url": url}},
            {"ok": True, "result": "routed-working", "url": url},
        ]) as ipc:
            result = loom_mcp.t_chat_route({"origin_ref": "mobile:known",
                "operation_key": "route-known-mobile", "lifecycle": "working"})
        self.assertTrue(result["ok"])
        commands = [call.args[0]["command"] for call in ipc.call_args_list]
        self.assertEqual(commands, ["origin-resolve", "origin-register", "chat-route"])
        self.assertNotIn("chat-origin-resolve", commands)

    def test_unknown_registered_origin_falls_back_only_with_exact_messages(self):
        url = "https://chatgpt.com/c/desktop-fallback"
        with patch.object(loom_mcp, "_ipc", side_effect=[
            {"ok": True, "result": "origin-not-registered", "origin": None},
            {"ok": True, "url": url},
            {"ok": True, "origin": {"canonical_url": url}},
            {"ok": True, "result": "routed-working", "url": url},
        ]) as ipc:
            result = loom_mcp.t_chat_route({"origin_ref": "desktop:new",
                "operation_key": "route-desktop-fallback",
                "origin_messages": ["An exact sufficiently long desktop message for safe matching"],
                "lifecycle": "working"})
        self.assertTrue(result["ok"])
        self.assertEqual(ipc.call_args_list[1].args[0]["command"], "chat-origin-resolve")

    def test_unknown_mobile_origin_without_url_or_messages_refuses(self):
        with patch.object(loom_mcp, "_ipc", return_value={"ok": True,
                "result": "origin-not-registered", "origin": None}) as ipc:
            with self.assertRaisesRegex(loom_mcp.ToolError, "Cannot identify"):
                loom_mcp.t_chat_route({"origin_ref": "mobile:unknown",
                    "operation_key": "route-mobile-unknown", "lifecycle": "working"})
        self.assertEqual(ipc.call_count, 1)

    def test_explicit_url_late_binds_before_move(self):
        url = "https://chatgpt.com/g/g-p-0123456789abcdef0123456789abcdef-working/c/exact-chat"
        with patch.object(loom_mcp, "_ipc", side_effect=[
            {"ok": True, "origin": {"canonical_url": url}},
            {"ok": True, "result": "routed-working", "url": url},
        ]) as ipc:
            loom_mcp.t_chat_route({"origin_ref": "mobile:late", "url": url,
                "operation_key": "bind-mobile-late", "lifecycle": "working"})
        self.assertEqual(ipc.call_args_list[0].args[0]["command"], "origin-register")
        self.assertEqual(ipc.call_args_list[1].args[0]["command"], "chat-route")


if __name__ == "__main__":
    unittest.main()
