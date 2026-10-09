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
        self.opens = []
        self.window_available = True

    def worker_create(self, task_id, prompt):
        self.creates += 1
        return {"ok": True, "href": "https://chatgpt.com/c/canonical-1",
                "projects": list(self.projects)}

    def worker_recover(self, task_id):
        return dict(self.recover)

    def worker_open(self, task_id, url, reload=False):
        self.opens.append((task_id, url, reload))
        self.window_available = True
        return {"ok": True}

    def worker_discover_projects(self, task_id):
        if not self.window_available:
            return {"ok": False, "result": "worker-actor-unavailable"}
        return {"ok": True, "projects": list(self.projects)}

    def worker_move_project(self, task_id, project_id, project_name):
        self.moves.append((task_id, project_id, project_name))
        return {"ok": True, "projectId": project_id, "projectName": project_name}

    def worker_resolve_project(self, name):
        for project in self.projects:
            if project["name"].casefold() == str(name).casefold():
                return {"ok": True, "id": project["id"], "name": name}
        return {"ok": False, "result": "project-not-found"}

    def worker_close(self, task_id):
        self.closed.append(task_id)
        return {"ok": True}

    def worker_latest_response(self, task_id):
        if not self.window_available:
            return {"ok": False, "result": "worker-actor-unavailable"}
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

    def test_old_browser_controller_is_rejected_before_any_chat_is_reserved(self):
        # Updated Sine scripts on disk do not replace the JS in a running
        # Zen process. A stale controller must not consume an idempotency key.
        self.zen.web_worker_bridge_ready = lambda: False
        outcome = self.manager.create_background(
            request_id="stale-browser", title="No launch", prompt="Never send",
            working_project="Working")
        self.assertFalse(outcome["ok"])
        self.assertIn("not yet loaded", outcome["error"])
        self.assertEqual(self.zen.creates, 0)
        self.assertIsNone(self.store.find_request("stale-browser"))

    def test_reusing_key_for_other_prompt_is_rejected(self):
        self.create()
        result = self.manager.create(request_id="req-1", title="Other", prompt="Different", working_project="Working")
        self.assertFalse(result["ok"])
        self.assertIn("different prompt", result["error"])

    def test_no_review_without_actual_assistant_text(self):
        task = self.create("no-empty-review")["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 10
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 1, "assistantText": ""}
        for _ in range(5):
            result = self.manager.inspect(task["id"])["task"]
        self.assertEqual(result["phase"], "running")

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

    def test_rejected_worker_is_terminal_for_idempotent_create(self):
        task = self.create("rejected-terminal")["task"]
        self.store.update(task["id"], status="waiting", phase="awaiting-review", response="old answer")
        rejected = self.manager.review(task_id=task["id"], decision="rejected", reviewer="r",
                                       evidence="unsafe result", done_project="Done")
        self.assertTrue(rejected["ok"])

        self.zen.latest = {"ok": True, "working": False, "assistantCount": 1,
                           "assistantText": "old answer"}
        retried = self.create("rejected-terminal")

        self.assertEqual(retried["result"], "existing")
        self.assertEqual((retried["task"]["status"], retried["task"]["phase"]),
                         ("blocked", "review-rejected"))
        self.assertEqual(self.zen.opens, [])
        self.assertEqual(len(self.zen.moves), 1)

    def test_running_transition_captures_current_assistant_count(self):
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 4,
                           "assistantText": "older response"}
        task = self.create("baseline")["task"]
        self.assertEqual(task["baselineAssistantCount"], 4)
        self.store._tasks[0]["createdAt"] = time.time() - 5

        for _ in range(4):
            observed = self.manager.observe(task["id"], self.zen.latest)

        self.assertEqual(observed["phase"], "running")

    def test_inspect_restores_closed_worker_from_canonical_url(self):
        task = self.create("inspect-restore")["task"]
        self.zen.window_available = False

        inspected = self.manager.inspect(task["id"])

        self.assertTrue(inspected["ok"])
        self.assertTrue(inspected["live"]["ok"])
        self.assertEqual(self.zen.opens[-1], (task["id"], task["url"], False))

    def test_review_restores_closed_worker_from_canonical_url(self):
        task = self.create("review-restore")["task"]
        self.store.update(task["id"], status="waiting", phase="awaiting-review", response="answer")
        self.zen.window_available = False

        reviewed = self.manager.review(task_id=task["id"], decision="approved", reviewer="r",
                                       evidence="verified", done_project="Done")

        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["task"]["phase"], "done")
        self.assertEqual(self.zen.opens[-1], (task["id"], task["url"], False))

    def test_created_chat_with_delayed_canonical_navigation_recovers_without_resending(self):
        recovered_url = "https://chatgpt.com/c/01234567-89ab-4cde-8fab-0123456789ab"
        self.zen.worker_create = lambda *_: {"ok": False, "result": "worker-created"}
        self.zen.recover = {"ok": True, "result": "worker-recovered",
                            "href": recovered_url, "projects": list(self.zen.projects)}
        result = self.create("delayed-canonical")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["url"], recovered_url)
        self.assertEqual(result["task"]["phase"], "running")

    def test_empty_project_resolves_from_sidebar_catalog(self):
        self.zen.projects = []
        self.zen.worker_resolve_project = lambda name: {
            "ok": True, "name": name, "id": "g-p-verified-from-sidebar"
        }
        task = self.create("empty-project")
        self.assertTrue(task["ok"])
        self.assertEqual(task["task"]["phase"], "running")
        self.assertEqual(task["task"]["projectId"], "g-p-verified-from-sidebar")

    def test_done_project_resolves_from_sidebar_catalog(self):
        task = self.create("empty-done-project")["task"]
        self.store.update(task["id"], phase="awaiting-review",
                          status="waiting", response="Smoke token")
        self.zen.projects = []
        self.zen.worker_resolve_project = lambda name: {
            "ok": True, "name": name, "id": "g-p-done-verified"
        }
        result = self.manager.review(task_id=task["id"], decision="approved",
                    reviewer="independent", evidence="verified response",
                    done_project="Done")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["projectId"], "g-p-done-verified")

    def test_reconcile_existing_recovers_url_without_prompt_submission(self):
        self.zen.projects = []
        self.zen.worker_create = lambda *_: {"ok": False, "result": "timeout"}
        self.assertFalse(self.create("recover-without-resend")["ok"])
        task = self.store.find_request("recover-without-resend")
        self.assertEqual(task["phase"], "creation-failed")
        self.zen.worker_create = lambda *_: self.fail("recovery resent a prompt")
        recovered_url = "https://chatgpt.com/c/01234567-89ab-4cde-8fab-0123456789ab"
        self.zen.recover = {"ok": True, "result": "worker-recovered",
                            "href": recovered_url, "projects": []}
        response = self.manager.reconcile_existing(task["id"])
        self.assertTrue(response["ok"])
        self.assertEqual(response["task"]["url"], recovered_url)
        self.assertEqual(response["task"]["phase"], "project-not-found")

    def test_bad_creation_never_records_unverified_url(self):
        self.zen.worker_create = lambda *_: {"ok": True, "href": "https://chatgpt.com/?local=1", "projects": []}
        result = self.create("bad")
        self.assertFalse(result["ok"])
        self.assertEqual((result["task"]["url"], result["task"]["phase"]), ("", "creation-failed"))

    def test_creation_failure_is_terminal_for_idempotent_create(self):
        self.zen.worker_create = lambda *_: {"ok": False, "result": "temporary"}
        first = self.create("retry")
        self.assertFalse(first["ok"])
        self.zen.worker_recover = lambda *_: self.fail("terminal failure was re-driven")
        retried = self.create("retry")
        self.assertTrue(retried["ok"])
        self.assertEqual((retried["result"], retried["task"]["phase"]),
                         ("existing", "creation-failed"))
        self.assertEqual(len([t for t in self.store.list() if t["requestId"] == "retry"]), 1)

    def test_ambiguous_send_failure_is_terminal_and_never_resends(self):
        self.zen.worker_create = lambda *_: {"ok": False, "result": "canonical-conversation-timeout"}
        self.assertFalse(self.create("ambiguous")["ok"])
        self.zen.worker_create = lambda *_: self.fail("retry resent the prompt")
        self.zen.recover = {"ok": False, "result": "canonical-conversation-recovery-timeout"}
        retry = self.create("ambiguous")
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["result"], "existing")
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
        self.assertEqual(self.zen.opens[-1][1], first["task"]["url"])

    def test_background_project_retry_reopens_canonical_url_after_window_loss(self):
        self.zen.projects = []
        first = self.create("background-project-retry")
        self.assertEqual(first["task"]["phase"], "project-not-found")
        self.zen.projects = [{"id": "working-id", "name": "Working"}]
        self.zen.worker_recover = lambda *_: self.fail("canonical retry used window-only recovery")
        completed = threading.Event()
        retry = self.manager.create_background(
            request_id="background-project-retry", title="Build", prompt="Do safe work",
            working_project="Working", on_complete=completed.set)
        self.assertEqual((retry["result"], retry["task"]["phase"]),
                         ("resuming", "project-not-found"))
        self.assertTrue(completed.wait(2))
        task = self.store.get(first["task"]["id"])
        self.assertEqual((task["phase"], task["projectName"]), ("running", "Working"))
        self.assertEqual(self.zen.opens[-1][1], first["task"]["url"])

    def test_generic_store_done_and_reopen_cannot_bypass_web_worker_review(self):
        task = self.create("generic-done-guard")["task"]
        with self.assertRaisesRegex(ValueError, "loom_web_worker_review"):
            self.store.complete(task["id"])
        with self.assertRaisesRegex(ValueError, "verified review"):
            self.store.update(task["id"], status="done")
        with self.assertRaisesRegex(ValueError, "loom_web_worker_review"):
            self.store.reopen(task["id"])
        unchanged = self.store.get(task["id"])
        self.assertEqual((unchanged["status"], unchanged["phase"], unchanged["reviewer"]),
                         ("working", "running", ""))

    def test_generic_backend_tools_reject_web_worker_without_closing_it(self):
        from backend import TabbyBackend
        task = self.create("backend-done-guard")["task"]
        backend = TabbyBackend.__new__(TabbyBackend)
        backend.working = self.store
        backend.voice = self.zen
        backend._working_idle_ticks = {}
        backend._working_retry_at = {}
        backend._publish_working = lambda: None
        attempts = (
            backend.work_complete(task["id"], "claimed done"),
            backend.work_update(task["id"], status="done"),
            backend.work_reopen(task["id"]),
        )
        self.assertTrue(all(not result["ok"] for result in attempts))
        unchanged = self.store.get(task["id"])
        self.assertEqual((unchanged["status"], unchanged["phase"], unchanged["reviewer"]),
                         ("working", "running", ""))
        self.assertEqual(self.zen.closed, [])

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
