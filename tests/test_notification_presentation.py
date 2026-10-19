import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import loom_mcp
from tabby.notifications import NotificationStore, deliver, validate_view_action

TARGET="http://100.76.220.113:8782/project/45d346b5-d5de-4ff2-92c5-be267e5a045c"

class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environ=patch.dict(os.environ,{"LOOM_NOTIFICATIONS_DB":str(Path(self.tmp.name)/"notes.db"),
                        "LOOM_QUIET_HOURS":"",
                        "LOOM_NTFY_URL":"https://example.test/private-topic",
                        "LOOM_NTFY_ICON_URL":"https://example.test/unwanted-giant-robot.png",
                        "LOOM_NTFY_TOKEN_FILE":""},clear=False)
        environ.start()
        self.addCleanup(environ.stop)

    def test_no_duplicate_large_icon_and_real_view_action(self):
        store=NotificationStore()
        item=store.create(title="SUMI · Project ready",body="Open your diagrams project.")
        class Accepted:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):return None
        with patch("tabby.notifications.urllib.request.urlopen",return_value=Accepted()) as request:
            result=deliver(item,store,action_url=TARGET,action_label="Open in SUMI ↗")
            payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(result["phone"],"accepted_by_provider")
        self.assertEqual(payload["title"],"SUMI · Project ready")
        self.assertNotIn("icon",payload)
        self.assertNotIn("tags",payload)
        self.assertEqual(payload["click"],TARGET)
        self.assertEqual(payload["actions"],[{"action":"view","label":"Open in SUMI ↗","url":TARGET}])
        self.assertTrue(store.get(item["id"])["phone_delivered"])

    def test_immediate_tool_response_reports_saved_phone_status(self):
        def accepted(item, store, **kwargs):
            store.delivered(item["id"], "phone")
            return {"desktop": "queued_for_backend", "phone": "accepted_by_provider"}
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(loom_mcp, "deliver", side_effect=accepted))
            stack.enter_context(patch.object(loom_mcp, "_ipc", return_value={"ok": True}))
            output = loom_mcp.call_tool("loom_notify", {
                "title": "SUMI · Verified",
                "body": "Open your project.",
                "request_id": "response-test-1",
                "action_url": TARGET,
                "action_label": "Open in SUMI ↗",
            })
        self.assertFalse(output["isError"])
        self.assertTrue(output["structuredContent"]["notification"]["created"])
        self.assertTrue(output["structuredContent"]["notification"]["phone_delivered"])

    def test_reject_preview_for_open_action(self):
        with self.assertRaisesRegex(ValueError,"manual-preview"):
            validate_view_action(TARGET+"?manual=1","Open in SUMI ↗")
        self.assertEqual(validate_view_action(TARGET+"?manual=1","Preview SUMI ↗")[0],TARGET+"?manual=1")

    def test_reject_non_tailnet_http_and_credentials(self):
        for url in ("http://example.com/project/123",
                    "http://127.0.0.1/private",
                    "https://me:secret@example.com/file",
                    "javascript:alert(1)"):
            with self.subTest(url=url),self.assertRaises(ValueError):
                validate_view_action(url,"Open ↗")
        self.assertEqual(validate_view_action("https://example.com/path","Open ↗")[1],"Open ↗")

    def test_tool_advertises_and_rejects_misrouted_action(self):
        tool=loom_mcp.TOOL_INDEX["loom_notify"]
        self.assertIn("action_url",tool[2]["properties"])
        with patch.object(loom_mcp,"deliver") as mocked:
            result=loom_mcp.call_tool("loom_notify",{"title":"SUMI","action_url":TARGET+"?manual=1","action_label":"Open in SUMI"})
        self.assertTrue(result["isError"])
        self.assertIn("manual-preview",result["structuredContent"]["error"])
        mocked.assert_not_called()

if __name__=="__main__":
    unittest.main()
