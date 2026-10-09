import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import loom_mcp
from backend import TabbyBackend
from tabby.notifications import NotificationStore, deliver, in_quiet_hours
from tabby.state import TabbyState


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "notif.db"
        self.env = patch.dict(os.environ, {"LOOM_NOTIFICATIONS_DB": str(self.path)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_persistence_dedupe_and_permissions(self):
        store = NotificationStore()
        note = store.create(title="Deployment", body="Build verified", kind="status", request_id="run-1")
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), "0o600")
        self.assertTrue(note["created"])
        repeat = NotificationStore().create(title="Deployment", request_id="run-1")
        self.assertEqual(repeat["id"], note["id"])
        self.assertFalse(repeat["created"])
        self.assertEqual(NotificationStore().get(note["id"])["body"], "Build verified")

    def test_decision_answer_survives_restart_and_is_single_use(self):
        note = NotificationStore().create(title="Pick plan", kind="choice", options=["A", "B"])
        ident = note["id"]
        with self.assertRaisesRegex(ValueError, "Invalid choice"):
            NotificationStore().respond(ident, "Z")
        answer = NotificationStore().respond(ident, "B")
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(NotificationStore().get(ident)["response"], "B")
        self.assertTrue(NotificationStore().respond(ident, "B")["idempotent"])
        with self.assertRaisesRegex(ValueError, "already answered"):
            NotificationStore().respond(ident, "A")

    def test_expiry_never_auto_approves(self):
        store = NotificationStore()
        note = store.create(title="Approve update", kind="approval", ttl_minutes=1)
        with store._db() as db:
            db.execute("UPDATE notifications SET expires_at=1 WHERE id=?", (note["id"],))
        self.assertEqual(store.get(note["id"])["status"], "expired")
        self.assertIsNone(store.get(note["id"])["response"])
        with self.assertRaisesRegex(ValueError, "no longer actionable"):
            store.respond(note["id"], "Approve")

    def test_dismissal_read_and_rejection(self):
        store = NotificationStore()
        note = store.create(title="Check", kind="action")
        self.assertEqual(store.mark(note["id"])["status"], "read")
        self.assertEqual(store.mark(note["id"], dismiss=True)["status"], "dismissed")
        with self.assertRaisesRegex(ValueError, "no choices"):
            store.respond(note["id"], "Yes")
        with self.assertRaisesRegex(ValueError, "Unknown notification kind"):
            store.create(title="X", kind="admin")

    def test_no_unconfigured_phone_claim(self):
        store = NotificationStore()
        note = store.create(title="Normal")
        with patch.dict(os.environ, {"LOOM_NTFY_URL": "", "DBUS_SESSION_BUS_ADDRESS": ""}):
            result = deliver(note, store)
        self.assertEqual(result["phone"], "not_configured")
        self.assertFalse(store.get(note["id"])["phone_delivered"])

    def test_mcp_and_backend_ui_separation(self):
        tools = {t["name"] for t in loom_mcp.tool_list()}
        self.assertTrue({"loom_notify", "loom_request_decision", "loom_notification_get",
                         "loom_notification_list"} <= tools)
        self.assertFalse(any("notification_answer" in name or "notification_respond" in name for name in tools))
        with patch.object(loom_mcp, "deliver", return_value={"desktop": "offline", "phone": "not_configured"}), \
             patch.object(loom_mcp, "_ipc", side_effect=loom_mcp.ToolError("Desktop offline")):
            result = loom_mcp.call_tool("loom_request_decision", {
                "title": "Approve plan", "kind": "approval",
                "request_id": "decision-1"})
        self.assertFalse(result["isError"])
        item = result["structuredContent"]["notification"]
        self.assertEqual(item["status"], "pending")
        b = TabbyBackend.__new__(TabbyBackend)
        b.state = TabbyState(True)
        changed = b.handle({"command": "notification-answer", "notification_id": item["id"], "option": "Reject"})
        self.assertTrue(changed["ok"])
        self.assertEqual(NotificationStore().get(item["id"])["response"], "Reject")
        self.assertEqual(b.state.snapshot()["notifications"], [])

    def test_quiet_hours_suppresses_nonurgent_delivery(self):
        import datetime
        self.assertTrue(in_quiet_hours("22:00-08:00", datetime.datetime(2026, 10, 9, 23, 0)))
        self.assertTrue(in_quiet_hours("22:00-08:00", datetime.datetime(2026, 10, 9, 7, 0)))
        self.assertFalse(in_quiet_hours("22:00-08:00", datetime.datetime(2026, 10, 9, 10, 0)))
        store = NotificationStore()
        note = store.create(title="Quiet status")
        with patch.dict(os.environ, {"LOOM_QUIET_HOURS": "00:00-23:59", "LOOM_NTFY_URL": "https://example.test/topic"}), \
             patch("tabby.notifications.urllib.request.urlopen") as request:
            output = deliver(note, store)
        request.assert_not_called()
        self.assertEqual(output["phone"], "quiet_hours")

    def test_provider_acceptance_is_not_phone_confirmation(self):
        store = NotificationStore()
        note = store.create(title="Milestone", body="Test complete", kind="status")
        class Accepted:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.dict(os.environ, {"LOOM_NTFY_URL": "https://example.test/topic",
                                     "LOOM_QUIET_HOURS": "", "DBUS_SESSION_BUS_ADDRESS": ""}), \
             patch("tabby.notifications.urllib.request.urlopen", return_value=Accepted()) as request:
            output = deliver(note, store)
            self.assertTrue(request.called)
        self.assertEqual(output["phone"], "accepted_by_provider")
        approval = store.create(title="Confirm", kind="approval")
        with patch.dict(os.environ, {"LOOM_NTFY_URL": "https://example.test/topic",
                                     "LOOM_QUIET_HOURS": "", "DBUS_SESSION_BUS_ADDRESS": ""}), \
             patch("tabby.notifications.urllib.request.urlopen") as request:
            output = deliver(approval, store)
            request.assert_not_called()
        self.assertEqual(output["phone"], "callback_not_configured")

    def test_rate_limit_rejects_flood(self):
        store = NotificationStore()
        for i in range(50):
            store.create(title="Status", request_id=f"flood-{i}")
        with self.assertRaisesRegex(ValueError, "Hourly notification limit"):
            store.create(title="One too many")
        self.assertTrue(store.create(title="Emergency", urgency="high")["created"])

    def test_bad_choice_arguments_are_rejected(self):
        store = NotificationStore()
        for options in (["A"], ["A", "A"], ["" , "B"]):
            with self.assertRaisesRegex(ValueError, "2..6 distinct"):
                store.create(title="Decision", kind="choice", options=options)
        with self.assertRaisesRegex(ValueError, "Options only"):
            store.create(title="Status", options=["A","B"])


if __name__ == "__main__":
    unittest.main()
