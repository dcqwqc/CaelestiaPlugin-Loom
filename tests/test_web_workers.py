import tempfile
import unittest
from pathlib import Path

from tabby.web_workers import WebWorkerManager, canonical_chat_url
from tabby.working import WorkingStore


class FakeZen:
    def __init__(self):
        self.creates = 0
        self.moves = []
        self.latest = {"ok": True, "working": True, "assistantCount": 0}

    def worker_create(self, task_id, prompt):
        self.creates += 1
        return {"ok": True, "href": "https://chatgpt.com/c/canonical-1",
                "projects": [{"id": "working-id", "name": "Working"}]}

    def worker_move_project(self, task_id, project_id, project_name):
        self.moves.append((task_id, project_id, project_name))
        return {"ok": True, "projectName": project_name}

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
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 1, "assistantText": "claimed done"}
        inspected = self.manager.inspect(task["id"])["task"]
        self.assertEqual((inspected["status"], inspected["phase"]), ("waiting", "awaiting-review"))
        reviewed = self.manager.review(task_id=task["id"], decision="approved", reviewer="review-agent-2",
            evidence="tests pass at commit abc", done_project="Done")
        self.assertTrue(reviewed["ok"])
        self.assertEqual((reviewed["task"]["status"], reviewed["task"]["projectName"]), ("done", "Done"))
        self.assertEqual(self.zen.moves[-1][2], "Done")

    def test_bad_creation_never_records_unverified_url(self):
        self.zen.worker_create = lambda *_: {"ok": True, "href": "https://chatgpt.com/?local=1", "projects": []}
        result = self.create("bad")
        self.assertFalse(result["ok"])
        self.assertEqual((result["task"]["url"], result["task"]["phase"]), ("", "creation-failed"))

    def test_failed_reservation_can_retry_same_worker_key(self):
        good = self.zen.worker_create
        self.zen.worker_create = lambda *_: {"ok": False, "result": "temporary"}
        self.assertFalse(self.create("retry")["ok"])
        self.zen.worker_create = good
        retried = self.create("retry")
        self.assertTrue(retried["ok"])
        self.assertEqual(len([t for t in self.store.list() if t["requestId"] == "retry"]), 1)

    def test_canonical_url_is_strict(self):
        self.assertTrue(canonical_chat_url("https://chatgpt.com/c/abc"))
        for value in ("https://evil.example/c/abc", "https://chatgpt.com/", "https://chatgpt.com/c/local-chatgpt:1"):
            self.assertFalse(canonical_chat_url(value))


if __name__ == "__main__":
    unittest.main()
