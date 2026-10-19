import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import loom_mcp


class HeadlessNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {"LOOM_NOTIFICATIONS_DB": str(Path(self.tmp.name)/"ledger.sqlite3")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_notifier_runs_without_desktop_and_deduplicates_provider(self):
        calls = []
        def fake_deliver(item, store):
            calls.append(item["id"])
            store.delivered(item["id"], "phone")
            return {"phone": "accepted_by_provider", "desktop": "not_configured"}
        with patch.object(loom_mcp, "deliver", fake_deliver):
            params = {"title":"SUMI ready","body":"Verified update","request_id":"release-1","kind":"action"}
            first = loom_mcp.call_tool("loom_notify", params)["structuredContent"]
            again = loom_mcp.call_tool("loom_notify", params)["structuredContent"]
            self.assertTrue(first["ok"])
            self.assertEqual(first["notification"]["id"], again["notification"]["id"])
            self.assertEqual(len(calls), 1)
            self.assertTrue(again["notification"]["phone_delivered"])
            self.assertEqual(first["delivery"]["phone"], "accepted_by_provider")
            self.assertEqual(again["delivery"]["phone"], "previously_accepted")
            self.assertEqual(first["delivery"]["desktop"], "desktop_client_optional")
            record = loom_mcp.call_tool("loom_notification_get",{"notification_id":first["notification"]["id"]})["structuredContent"]
            self.assertEqual(record["notification"]["title"], "SUMI ready")
            self.assertEqual(len(loom_mcp.call_tool("loom_notification_list",{"limit":8})["structuredContent"]["notifications"]),1)

    def test_failed_push_can_retry_with_same_key_without_duplicating_row(self):
        outcomes = iter(["failed", "accepted_by_provider"])
        def flaky(item, store):
            result = next(outcomes)
            if result=="accepted_by_provider":store.delivered(item["id"],"phone")
            return {"phone":result}
        params={"title":"Reconnect","request_id":"offline-retry"}
        with patch.object(loom_mcp, "deliver", flaky):
            a=loom_mcp.call_tool("loom_notify",params)["structuredContent"]
            b=loom_mcp.call_tool("loom_notify",params)["structuredContent"]
        self.assertEqual(a["notification"]["id"],b["notification"]["id"])
        self.assertEqual(a["delivery"]["phone"],"failed")
        self.assertEqual(b["delivery"]["phone"],"accepted_by_provider")

    def test_does_not_require_mirai_adapter(self):
        with patch.object(loom_mcp.desktop_adapter, "call_named_tool", side_effect=AssertionError("Mirai adapter used")):
            with patch.object(loom_mcp, "deliver", return_value={"phone":"not_configured"}):
                result=loom_mcp.call_tool("loom_notify",{"title":"Philipedia only"})["structuredContent"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery"]["phone"],"not_configured")


if __name__=="__main__":
    unittest.main()
