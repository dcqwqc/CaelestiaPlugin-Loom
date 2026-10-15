import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tabby.notifications import NotificationStore


class TaskNoticeExpiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "notes.sqlite3"

    def test_newest_task_notice_supersedes_earlier_status(self):
        with patch("tabby.notifications.time.time", return_value=10000):
            store = NotificationStore(self.db)
            older = store.create(title="Task A", kind="status", request_id="agent-event-old",
                                 task_id="task-a", task_status="working", event_at=9990)
            decision = store.create(title="Approve", kind="approval", options=["Yes", "No"])
        with patch("tabby.notifications.time.time", return_value=10040):
            blocked = store.create(title="Task A blocked", kind="status",
                                   request_id="agent-event-blocked", task_id="task-a",
                                   task_status="blocked", event_at=10038)
            self.assertEqual(store.get(older["id"])["status"], "expired")
            self.assertEqual(store.get(blocked["id"])["task_id"], "task-a")
            self.assertEqual(store.get(blocked["id"])["task_status"], "blocked")
            self.assertEqual([x["id"] for x in store.list(include_closed=False)],
                             [blocked["id"], decision["id"]])
            self.assertFalse(store.create(title="Old callback", kind="status",
                request_id="agent-event-old-late", task_id="task-a",
                task_status="working", event_at=9991)["created"])
        with patch("tabby.notifications.time.time", return_value=11000):
            self.assertEqual([x["id"] for x in store.list(include_closed=False)],
                             [decision["id"]])

    def test_legacy_task_alert_expires_but_other_alerts_survive(self):
        with patch("tabby.notifications.time.time", return_value=10000):
            store = NotificationStore(self.db)
            legacy = store.create(title="Legacy worker",kind="status",request_id="agent-event-legacy")
            normal = store.create(title="Other status",kind="status",request_id="monitor-1")
            action = store.create(title="Action",kind="action")
        with patch("tabby.notifications.time.time", return_value=11000):
            self.assertEqual(store.get(legacy["id"])["status"], "expired")
            self.assertEqual({x["id"] for x in store.list(include_closed=False)},
                             {normal["id"], action["id"]})

    def test_existing_database_is_migrated_without_losing_notifications(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""CREATE TABLE notifications(
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, title TEXT NOT NULL,
                body TEXT NOT NULL, kind TEXT NOT NULL, urgency TEXT NOT NULL,
                options TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, expires_at INTEGER, response TEXT,
                desktop_delivered INTEGER NOT NULL DEFAULT 0,
                phone_delivered INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""INSERT INTO notifications
                (id,request_key,title,body,kind,urgency,options,status,
                 created_at,updated_at)
                VALUES ('existing','regular','Keep me','', 'info','normal','[]',
                        'unread',10000,10000)""")
        with patch("tabby.notifications.time.time", return_value=10001):
            store = NotificationStore(self.db)
            self.assertEqual(store.get("existing")["title"], "Keep me")
            task = store.create(title="Task", kind="status", request_id="agent-event-1",
                task_id="task-a", task_status="blocked", event_at=10001)
            self.assertEqual(store.get(task["id"])["task_id"], "task-a")

    def test_old_source_event_never_reappears_as_fresh(self):
        with patch("tabby.notifications.time.time", return_value=10000):
            store=NotificationStore(self.db)
            result=store.create(title="Already old",kind="status",
                request_id="agent-event-old",task_id="task-a",task_status="working",
                event_at=1000)
            self.assertEqual(result,{"created":False,"expired":True})
            self.assertEqual(store.list(include_closed=False),[])


if __name__ == "__main__":
    unittest.main()
