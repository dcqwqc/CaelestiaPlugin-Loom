"""Regression coverage for the Loom bar's per-card dismissal path."""
import io
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend import TabbyBackend
from tabby.notifications import NotificationStore
from tabby.state import TabbyState


ROOT = Path(__file__).resolve().parents[1]


class LoomNotificationDismissTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {"LOOM_NOTIFICATIONS_DB": str(Path(directory.name) / "notes.sqlite3")})
        env.start()
        self.addCleanup(env.stop)

    def invoke_cli(self, *args):
        output = io.StringIO()
        with patch("tabby.ipc.send_command", return_value={"ok": True}) as send, \
             patch.object(sys, "argv", [str(ROOT / "loomctl.py"), *args]), \
             redirect_stdout(output), \
             self.assertRaises(SystemExit) as finished:
            runpy.run_path(str(ROOT / "loomctl.py"), run_name="__main__")
        self.assertEqual(finished.exception.code, 0)
        return send.call_args.args[0]

    def test_x_routes_clicked_notification_id_to_backend(self):
        self.assertEqual(
            self.invoke_cli("notification-dismiss", "chosen-id"),
            {"command": "notification-dismiss", "notification_id": "chosen-id"},
        )

    def test_decision_button_routes_notification_id_and_option(self):
        self.assertEqual(
            self.invoke_cli("notification-answer", "decision-id", "Not now"),
            {"command": "notification-answer", "notification_id": "decision-id", "option": "Not now"},
        )

    def test_dismiss_one_card_keeps_others_and_history(self):
        store = NotificationStore()
        first = store.create(title="Loom native notification live test", kind="status")
        second = store.create(title="test you choose needs attention", kind="choice",
                              options=["Approve", "Reject"])
        other = store.create(title="Other active", kind="info")
        backend = TabbyBackend.__new__(TabbyBackend)
        backend.state = TabbyState(True)
        result = backend.handle(self.invoke_cli("notification-dismiss", first["id"]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["notification"]["id"], first["id"])
        self.assertEqual(result["notification"]["status"], "dismissed")
        self.assertEqual(store.get(first["id"])["status"], "dismissed")
        self.assertEqual(store.get(second["id"])["status"], "pending")
        self.assertEqual(store.get(other["id"])["status"], "unread")
        self.assertEqual({note["id"] for note in backend.state.snapshot()["notifications"]},
                         {second["id"], other["id"]})
        self.assertEqual({note["id"] for note in store.list(include_closed=True)},
                         {first["id"], second["id"], other["id"]})

    def test_x_has_nonzero_layout_hit_target_and_tap_handler(self):
        panel = (ROOT / "Panel.qml").read_text()
        beginning = panel.index('id: notificationEntry')
        ending = panel.index('text: String(notificationEntry.entry.body', beginning)
        x = panel[beginning:ending]
        self.assertIn("Layout.preferredWidth: 22", x)
        self.assertIn("Layout.preferredHeight: 22", x)
        self.assertIn('onTapped: root.notificationDismiss(String(notificationEntry.entry.id || ""))', x)


if __name__ == "__main__":
    unittest.main()
