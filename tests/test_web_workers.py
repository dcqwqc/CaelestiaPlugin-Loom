import tempfile
import threading
import time
import unittest
from pathlib import Path

from tabby.web_workers import WebWorkerManager, canonical_chat_url
from tabby.working import WorkingStore


class FakeZen:
    def __init__(self):
        self.creates = 0
        self.moves = []
        self.latest = {"ok": True, "working": True, "assistantCount": 0}
        self.projects = [{"id": "working-id", "name": "Working"},
                         {"id": "done-id", "name": "Done"}]
        self.recover = {"ok": False, "result": "not configured"}
        self.closed = []

    def worker_create(self, task_id, prompt):
        self.creates += 1
        return {"ok": True, "href": "https://chatgpt.com/c/canonical-1",
                "projects": list(self.projects)}

    def worker_recover(self, task_id):
        return dict(self.recover)

    def worker_discover_projects(self, task_id):
        return {"ok": True, "projects": list(self.projects)}

    def worker_move_project(self, task_id, project_id, project_name):
        self.moves.append((task_id, project_id, project_name))
        return {"ok": True, "projectId": project_id, "projectName": project_name}

    def worker_close(self, task_id):
        self.closed.append(task_id)
        return {"ok": True}

    def worker_latest_response(self, task_id):
        return dict(self.latest)


class WebWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkingStore(Path(self.tmp.name) / "working.json")
        self.zen = FakeZen()
        self.manager = WebWorkerManager(self.store, self.zen)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, request_id="req-1"):
        return self.manager.create(request_id=request_id, title="Build it", prompt="Do safe work",
                                   working_project="Working")

    def test_create_is_durable_and_idempotent(self):
        first = self.create()
        second = self.create()
        self.assertEqual((first["result"], second["result"]), ("created", "existing"))
        self.assertEqual(self.zen.creates, 1)
        self.assertEqual(first["task"]["url"], "https://chatgpt.com/c/canonical-1")
        reloaded = WorkingStore(Path(self.tmp.name) / "working.json").find_request("req-1")
        self.assertEqual((reloaded["kind"], reloaded["phase"], reloaded["projectName"]),
                         ("web-worker", "running", "Working"))

    def test_reusing_key_for_other_prompt_is_rejected(self):
        self.create()
        result = self.manager.create(request_id="req-1", title="Other", prompt="Different", working_project="Working")
        self.assertFalse(result["ok"])
        self.assertIn("different prompt", result["error"])

    def test_response_waits_for_review_then_verified_done_move(self):
        task = self.create()["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 5
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 1, "assistantText": "claimed done"}
        for _ in range(3):
            inspected = self.manager.inspect(task["id"])["task"]
        self.assertEqual((inspected["status"], inspected["phase"]), ("waiting", "awaiting-review"))
        reviewed = self.manager.review(task_id=task["id"], decision="approved", reviewer="review-agent-2",
            evidence="tests pass at commit abc", done_project="Done")
        self.assertTrue(reviewed["ok"])
        self.assertEqual((reviewed["task"]["status"], reviewed["task"]["projectName"]), ("done", "Done"))
        self.assertEqual(self.zen.moves[-1][1:], ("done-id", "Done"))
        self.assertEqual(self.zen.closed, [task["id"]])

    def test_bad_creation_never_records_unverified_url(self):
        self.zen.worker_create = lambda *_: {"ok": True, "href": "https://chatgpt.com/?local=1", "projects": []}
        result = self.create("bad")
        self.assertFalse(result["ok"])
        self.assertEqual((result["task"]["url"], result["task"]["phase"]), ("", "creation-failed"))

    def test_failed_reservation_can_retry_same_worker_key(self):
        self.zen.worker_create = lambda *_: {"ok": False, "result": "temporary"}
        self.assertFalse(self.create("retry")["ok"])
        self.zen.recover = {"ok": True, "href": "https://chatgpt.com/c/recovered",
                            "projects": list(self.zen.projects)}
        retried = self.create("retry")
        self.assertTrue(retried["ok"])
        self.assertEqual(len([t for t in self.store.list() if t["requestId"] == "retry"]), 1)

    def test_retry_after_ambiguous_send_only_recovers_and_never_resends(self):
        self.zen.worker_create = lambda *_: {"ok": False, "result": "canonical-conversation-timeout"}
        self.assertFalse(self.create("ambiguous")["ok"])
        self.zen.worker_create = lambda *_: self.fail("retry resent the prompt")
        self.zen.recover = {"ok": False, "result": "canonical-conversation-recovery-timeout"}
        retry = self.create("ambiguous")
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["task"]["phase"], "creation-failed")

    def test_project_failure_retries_discovery_and_move_without_new_chat(self):
        self.zen.projects = []
        first = self.create("project-retry")
        self.assertFalse(first["ok"])
        self.assertEqual(first["task"]["phase"], "project-not-found")
        self.zen.projects = [{"id": "working-id", "name": "WORKING"}]
        self.zen.worker_create = lambda *_: self.fail("retry created another chat")
        retry = self.create("project-retry")
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["task"]["phase"], "running")

    def test_done_requires_discovered_id_and_verified_move(self):
        task = self.create("done-verify")["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 5
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 1}
        for _ in range(3):
            self.manager.inspect(task["id"])
        self.zen.projects = [{"id": "done-id", "name": "Done"}]
        self.zen.worker_move_project = lambda *_: {"ok": True, "projectId": "wrong", "projectName": "Done"}
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="r",
                                     evidence="verified", done_project="Done")
        self.assertFalse(result["ok"])
        self.assertNotEqual(self.store.get(task["id"])["phase"], "done")

    def test_transient_idle_does_not_complete_and_review_response_refreshes(self):
        task = self.create("debounce")["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 5
        idle = {"ok": True, "working": False, "assistantCount": 1, "assistantText": "partial"}
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "running")
        self.manager.observe(task["id"], {**idle, "working": True})
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "running")
        self.manager.observe(task["id"], idle)
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "awaiting-review")
        refreshed = self.manager.observe(task["id"], {**idle, "assistantText": "complete response"})
        self.assertEqual(refreshed["response"], "complete response")

    def test_background_create_returns_reserved_task_before_browser_finishes(self):
        release = threading.Event()
        original = self.zen.worker_create
        self.zen.worker_create = lambda *args: (release.wait(2), original(*args))[1]
        completed = threading.Event()
        started = time.monotonic()
        result = self.manager.create_background(request_id="background", title="Build", prompt="Do it",
                                                working_project="Working", on_complete=completed.set)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual((result["result"], result["task"]["phase"]), ("sending", "sending"))
        release.set()
        self.assertTrue(completed.wait(2))
        self.assertEqual(self.store.get(result["task"]["id"])["phase"], "running")

    def test_background_resumes_unsent_reservation_by_creating_once(self):
        task, _ = self.store.create_web_worker("reserved", "Build", "Do it")
        completed = threading.Event()
        result = self.manager.create_background(request_id="reserved", title="Build", prompt="Do it",
                                                working_project="Working", on_complete=completed.set)
        self.assertEqual(result["task"]["id"], task["id"])
        self.assertTrue(completed.wait(2))
        self.assertEqual(self.zen.creates, 1)
        self.assertEqual(self.store.get(task["id"])["phase"], "running")

    def test_worker_watchdogs_cover_bridge_deadlines_and_fit_client_timeouts(self):
        root = Path(__file__).resolve().parents[1]
        bridge = (root / "bridge/zen/.hey-tabby.uc.js").read_text()
        expected = {
            "worker-create": (33000, 30000, 35000),
            "worker-recover": (22000, 18000, 25000),
            "worker-move-project": (11000, 9000, 12000),
            "worker-discover-projects": (4500, 3500, 5000),
        }
        for command, (watchdog, internal, client) in expected.items():
            self.assertIn(f'if (name === "{command}") return {watchdog};', bridge)
            self.assertGreater(watchdog, internal)
            self.assertLess(watchdog, client)

    def test_canonical_url_is_strict(self):
        self.assertTrue(canonical_chat_url("https://chatgpt.com/c/abc"))
        self.assertTrue(canonical_chat_url("https://chatgpt.com/g/working-id/c/abc"))
        for value in ("https://evil.example/c/abc", "https://chatgpt.com/", "https://chatgpt.com/c/local-chatgpt:1"):
            self.assertFalse(canonical_chat_url(value))


if __name__ == "__main__":
    unittest.main()
