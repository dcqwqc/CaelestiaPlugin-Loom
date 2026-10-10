import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tabby.chat_projects import (DEFAULT_PROJECT_NAMES, chat_route, load_project_names,
                                 project_core_id, resolve_all, resolve_project)
from tabby.web_workers import MAX_SEND_ATTEMPTS, WebWorkerManager, canonical_chat_url
from tabby.working import WorkingStore

ALL = ("new", "vault", "working", "review", "blocked", "done")
HEX = {name: f"{i:032x}" for i, name in enumerate(ALL, 1)}


def project(lifecycle, name=None):
    core = f"g-p-{HEX[lifecycle]}"
    return {"id": core, "segment": f"{core}-{lifecycle}", "name": name or DEFAULT_PROJECT_NAMES[lifecycle]}


class FakeZen:
    """Models the browser the bridge drives: projects, chats, windows, turns.

    It enforces the same guards as the page actor (route and user-count checks
    before a send) so tests observe real ordering and delivery counts.
    """

    def __init__(self):
        self.projects = [project(k) for k in ALL]
        self.chats = {}      # conversation id -> {"project": core id, "users": [texts]}
        self.windows = {}    # task id -> href
        self.events = []
        self.closed = []
        self.resolver_closed = []
        self.voice_active = False
        self.engine_href = ""
        self.bootstrap_mode = "ok"     # ok | not-sent | unknown-created | unknown-lost
        self.move_mode = "ok"          # ok | claim-only | wrong-id | fail
        self.send_modes = []           # queue of ok | not-sent | unknown-delivered | unknown-lost
        self.latest = {"ok": True, "working": False, "assistantCount": 1, "assistantText": "READY"}

    # helpers --------------------------------------------------------------
    def _segment(self, core):
        return next((p["segment"] for p in self.projects if p["id"] == core), core)

    def _href(self, conv):
        core = self.chats[conv]["project"]
        return f"https://chatgpt.com/g/{self._segment(core)}/c/{conv}" if core else f"https://chatgpt.com/c/{conv}"

    def _conv(self, task_id):
        route = chat_route(self.windows.get(task_id, ""))
        return route["conversationId"] if route else ""

    def deliveries(self, text):
        return sum(users.count(text) for users in (c["users"] for c in self.chats.values()))

    def names(self, kind):
        return [e for e in self.events if e[0] == kind]

    # bridge API -------------------------------------------------------------
    def web_worker_bridge_ready(self):
        return True

    def worker_prepare(self, task_id):
        self.events.append(("prepare", task_id))
        href = self.windows.setdefault(task_id, "https://chatgpt.com/?loom-worker=1")
        return {"ok": True, "href": href if chat_route(href) else "", "projects": list(self.projects)}

    def worker_bootstrap(self, task_id, segment, project_id, text):
        self.events.append(("bootstrap", task_id, project_id, text))
        if self.bootstrap_mode == "not-sent":
            return {"ok": False, "sent": False, "result": "composer-not-found"}
        if self.bootstrap_mode == "unknown-lost":
            self.windows.pop(task_id, None)
            return {"ok": False, "sent": "unknown", "result": "bootstrap-canonical-timeout"}
        conv = f"conv-{len(self.chats) + 1}"
        self.chats[conv] = {"project": project_id, "users": [text]}
        self.windows[task_id] = self._href(conv)
        if self.bootstrap_mode == "unknown-created":
            return {"ok": False, "sent": "unknown", "result": "bootstrap-canonical-timeout"}
        return {"ok": True, "sent": True, "href": self.windows[task_id]}

    def worker_status(self, task_id):
        if task_id not in self.windows:
            return {"ok": False, "result": "worker-actor-unavailable"}
        return {"ok": True, "href": self.windows[task_id]}

    def worker_open(self, task_id, url, reload=False):
        self.events.append(("open", task_id, url, reload))
        route = chat_route(url)
        if not route or route["conversationId"] not in self.chats:
            return {"ok": False, "result": "invalid-chat-url"}
        self.windows[task_id] = self._href(route["conversationId"])
        return {"ok": True}

    def worker_resolve_projects(self, task_id, names):
        self.events.append(("resolve", task_id, tuple(names)))
        href = self.windows.get(task_id)
        if href is None:
            return {"ok": False, "result": "worker-actor-unavailable"}
        if chat_route(href):
            return {"ok": False, "result": "refusing-to-navigate-chat-window"}
        wanted = {n.casefold() for n in names}
        found = [p for p in self.projects if p["name"].casefold() in wanted]
        missing = [{"name": n, "result": "project-control-not-found"} for n in names
                   if not any(p["name"].casefold() == n.casefold() for p in found)]
        return {"ok": True, "projects": found, "errors": missing}

    def worker_discover_projects(self, task_id):
        if task_id not in self.windows:
            return {"ok": False, "result": "worker-actor-unavailable"}
        return {"ok": True, "projects": list(self.projects)}

    def worker_move_project(self, task_id, project_id, project_name, conversation_id=""):
        self.events.append(("move", task_id, project_id, project_name))
        conv = self._conv(task_id)
        if self.move_mode == "fail" or not conv or conv != conversation_id:
            return {"ok": False, "result": "move-project-control-not-found"}
        if self.move_mode == "claim-only":
            return {"ok": True, "projectId": project_id, "projectName": project_name, "conversationId": conv}
        if self.move_mode == "wrong-id":
            return {"ok": True, "projectId": "g-p-" + "f" * 32, "projectName": project_name, "conversationId": conv}
        self.chats[conv]["project"] = project_id
        self.windows[task_id] = self._href(conv)
        return {"ok": True, "projectId": project_id, "projectName": project_name, "conversationId": conv}

    def worker_turns(self, task_id):
        conv = self._conv(task_id)
        if not conv:
            return {"ok": False, "result": "worker-actor-unavailable"}
        users = self.chats[conv]["users"]
        return {"ok": True, "userCount": len(users), "lastUserText": users[-1] if users else "",
                "href": self.windows[task_id]}

    def worker_send_prompt(self, task_id, *, conversation_id, project_id, prompt, send_key, expected_user_count):
        self.events.append(("send", task_id, conversation_id, project_id, send_key))
        conv = self._conv(task_id)
        if conv != conversation_id or self.chats[conv]["project"] != project_id:
            return {"ok": False, "sent": False, "result": "route-mismatch"}
        if len(self.chats[conv]["users"]) != expected_user_count:
            return {"ok": False, "sent": False, "result": "user-count-mismatch"}
        mode = self.send_modes.pop(0) if self.send_modes else "ok"
        if mode == "not-sent":
            return {"ok": False, "sent": False, "result": "send-button-not-found"}
        if mode in {"ok", "unknown-delivered"}:
            self.chats[conv]["users"].append(prompt)
        if mode == "ok":
            return {"ok": True, "sent": True, "result": "prompt-sent"}
        return {"ok": False, "sent": "unknown", "result": "zen-bridge-timeout"}

    def worker_latest_response(self, task_id):
        if task_id not in self.windows:
            return {"ok": False, "result": "worker-actor-unavailable"}
        return {"href": self.windows[task_id], **self.latest}

    def worker_close(self, task_id):
        if not task_id.startswith("projects-"):  # throwaway resolver windows
            self.closed.append(task_id)
        else:
            self.resolver_closed.append(task_id)
        self.windows.pop(task_id, None)
        return {"ok": True}

    def status(self):
        return {"ok": True, "href": self.engine_href, "active": self.voice_active}


class Base(unittest.TestCase):
    PROMPT = "Implement the feature and run the tests"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "working.json"
        self.store = WorkingStore(self.path)
        self.zen = FakeZen()
        self.manager = WebWorkerManager(self.store, self.zen, project_names=dict(DEFAULT_PROJECT_NAMES),
                                       reconcile_delay=0)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, request_id="req-1", prompt=None, **kw):
        return self.manager.create(request_id=request_id, title="Build it", prompt=prompt or self.PROMPT,
                                   created_by=kw.pop("created_by", "claude-worker"), **kw)

    def to_review(self, task):
        """Put a running worker into a verified Review state with matching live output."""
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 2, "assistantText": "done"}
        self.store.update(task["id"], status="waiting", phase="awaiting-review", response="done",
                          lifecycle="review", projectId=project("review")["id"], projectName="Review")
        return self.store.get(task["id"])

    def settle(self, task, text="claimed done"):
        """Let a running worker's response settle through observe()."""
        self.store._tasks[0]["createdAt"] = time.time() - 5
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 2, "assistantText": text}
        for _ in range(3):
            observed = self.manager.inspect(task["id"])["task"]
        return observed


class MoveFirstCreationTests(Base):
    def test_transient_missing_chat_menu_recovers_without_resending(self):
        original = self.zen.worker_move_project
        calls = []
        def delayed(task_id, project_id, project_name, conversation_id=""):
            calls.append(conversation_id)
            if len(calls) == 1:
                return {"ok": False, "result": "move-project-control-not-found"}
            return original(task_id, project_id, project_name, conversation_id)
        self.zen.worker_move_project = delayed
        result = self.create(request_id="delayed-menu")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        self.assertEqual(chat_route(result["task"]["url"])["projectId"], project("working")["id"])

    def test_move_rejects_conversation_switch_before_click(self):
        conv = "conv-identity"
        self.zen.chats[conv] = {"project": project("new")["id"], "users": ["READY"]}
        self.zen.windows["identity-test"] = "https://chatgpt.com/c/other-conversation"
        href, error = self.manager._move_verified("identity-test", conv, project("working"))
        self.assertEqual(href, "")
        self.assertIn("identity changed", error)
        self.assertEqual(self.zen.names("move"), [])

    def test_bootstrap_resolves_new_only_then_discovers_working_later(self):
        result = self.create(request_id="single-project-discovery")
        self.assertTrue(result["ok"],result)
        calls = self.zen.names("resolve")
        self.assertEqual(calls[0][2],("New",))
        self.assertIn(("Working",),[call[2] for call in calls[1:]])

    def test_verified_sidebar_project_ids_skip_slow_navigation_resolver(self):
        old = self.zen.worker_discover_projects
        def fast(task_id):
            result = old(task_id)
            for entry in result.get('projects', []):
                entry['via'] = 'sidebar-row-id'
            return result
        self.zen.worker_discover_projects = fast
        result = self.create(request_id='sidebar-ids-fast')
        self.assertTrue(result['ok'],result)
        calls = self.zen.names('resolve')
        self.assertEqual([c[2] for c in calls], [('New',)])
        self.assertEqual(result['task']['projectId'],project('working')['id'])

    def test_prompt_is_sent_only_after_verified_move_to_working(self):
        result = self.create()
        self.assertTrue(result["ok"], result)
        task = result["task"]
        kinds = [e[0] for e in self.zen.events if e[0] in {"bootstrap", "move", "send"}]
        self.assertEqual(kinds, ["bootstrap", "move", "send"])
        bootstrap = self.zen.names("bootstrap")[0]
        self.assertEqual(bootstrap[2], project("new")["id"])
        self.assertNotIn(self.PROMPT, bootstrap[3])
        self.assertEqual(self.zen.names("move")[0][2:], (project("working")["id"], "Working"))
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        self.assertEqual((task["phase"], task["status"], task["lifecycle"]), ("running", "working", "working"))
        self.assertEqual(task["projectId"], project("working")["id"])
        self.assertEqual(chat_route(task["url"])["projectId"], project("working")["id"])
        self.assertEqual(task["expectedUserCount"], 1)

    def test_state_is_durable_and_create_is_idempotent(self):
        first = self.create()
        second = self.create()
        self.assertEqual((first["result"], second["result"]), ("created", "existing"))
        self.assertEqual(len(self.zen.names("bootstrap")), 1)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        reloaded = WorkingStore(self.path).find_request("req-1")
        self.assertEqual((reloaded["phase"], reloaded["lifecycle"], reloaded["createdBy"]),
                         ("running", "working", "claude-worker"))

    def test_reusing_key_for_other_prompt_is_rejected(self):
        self.create()
        result = self.create(prompt="Different")
        self.assertFalse(result["ok"])
        self.assertIn("different prompt", result["error"])

    def test_missing_lifecycle_projects_block_before_any_chat_exists(self):
        self.zen.projects = [p for p in self.zen.projects if p["name"] not in {"Vault", "Blocked"}]
        result = self.create()
        self.assertFalse(result["ok"])
        self.assertEqual(result["task"]["phase"], "project-not-found")
        self.assertIn("Vault", result["error"])
        self.assertIn("Blocked", result["error"])
        self.assertEqual((self.zen.names("bootstrap"), self.zen.chats), ([], {}))
        # Once the user creates the projects, the same request proceeds once.
        self.zen.projects = [project(k) for k in ALL]
        retry = self.create()
        self.assertTrue(retry["ok"])
        self.assertEqual(len(self.zen.chats), 1)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_ambiguous_project_names_fail_closed(self):
        self.zen.projects.append({"id": "g-p-" + "a" * 32, "segment": "g-p-" + "a" * 32, "name": "working"})
        result = self.create()
        self.assertFalse(result["ok"])
        self.assertIn("ambiguous", result["error"])
        self.assertEqual(self.zen.chats, {})

    def test_project_ids_are_resolved_without_navigating_a_chat_window(self):
        task = self.create()["task"]
        resolves = self.zen.names("resolve")
        # Validate all names without navigation, then resolve only New ID.
        self.assertEqual(resolves[0][1], task["id"])
        self.assertEqual(resolves[0][2], ('New',))
        # ... and every later resolution uses a throwaway window
        self.assertTrue(all(r[1].startswith("projects-") for r in resolves[1:]))
        self.manager.route(task_id=task["id"], lifecycle="vault", reason="archive")
        self.assertTrue(all(r[1] != task["id"] for r in self.zen.names("resolve")[1:]))
        handles = {r[1] for r in self.zen.names("resolve")[1:]}
        self.assertEqual(handles, set(self.zen.resolver_closed))  # no leaked windows

    def test_resolver_failure_reason_is_reported(self):
        self.zen.projects = [p for p in self.zen.projects if p["name"] != "Done"]
        result = self.create()
        self.assertIn("ChatGPT project not found: Done (project-control-not-found)", result["error"])

    def test_unverified_move_never_sends_and_retry_reuses_chat(self):
        self.zen.move_mode = "claim-only"   # bridge says ok, route disagrees
        first = self.create()
        self.assertFalse(first["ok"])
        self.assertEqual(first["task"]["phase"], "working-move-failed")
        self.assertEqual(self.zen.names("send"), [])
        self.assertEqual(self.zen.deliveries(self.PROMPT), 0)
        self.zen.move_mode = "ok"
        retry = self.create()
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(len(self.zen.chats), 1)
        self.assertEqual(len(self.zen.names("bootstrap")), 1)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_move_reporting_wrong_project_id_is_rejected(self):
        self.zen.move_mode = "wrong-id"
        result = self.create()
        self.assertFalse(result["ok"])
        self.assertEqual(result["task"]["phase"], "working-move-failed")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 0)

    def test_retry_after_window_loss_reopens_stored_conversation(self):
        self.zen.move_mode = "fail"
        first = self.create()
        self.zen.windows.clear()
        self.zen.move_mode = "ok"
        retry = self.create()
        self.assertTrue(retry["ok"])
        self.assertIn(("open", first["task"]["id"], first["task"]["url"], False), self.zen.events)
        self.assertEqual(len(self.zen.chats), 1)

    def test_definite_bootstrap_failure_is_retryable_without_duplicates(self):
        self.zen.bootstrap_mode = "not-sent"
        first = self.create()
        self.assertEqual(first["task"]["phase"], "bootstrap-failed")
        self.assertEqual(self.zen.chats, {})
        self.zen.bootstrap_mode = "ok"
        self.assertTrue(self.create()["ok"])
        self.assertEqual(len(self.zen.chats), 1)

    def test_unknown_bootstrap_adopts_chat_still_in_worker_window(self):
        self.zen.bootstrap_mode = "unknown-created"
        first = self.create()
        self.assertEqual(first["task"]["phase"], "bootstrap-unverified")
        self.zen.bootstrap_mode = "ok"
        retry = self.create()
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(len(self.zen.names("bootstrap")), 1)
        self.assertEqual(len(self.zen.chats), 1)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        self.assertEqual(retry["task"]["lifecycle"], "working")

    def test_adopted_chat_outside_new_is_not_labelled_new(self):
        self.zen.chats["stray"] = {"project": "", "users": ["setup"]}
        task, _ = self.store.create_web_worker("adopt", "Build", self.PROMPT)
        self.zen.windows[task["id"]] = "https://chatgpt.com/c/stray"
        self.store.update(task["id"], phase="bootstrap-unverified")
        self.zen.move_mode = "fail"
        result = self.create("adopt")
        self.assertEqual(result["task"]["phase"], "working-move-failed")
        self.assertEqual((result["task"]["lifecycle"], result["task"]["projectId"]), ("", ""))
        self.assertEqual(self.zen.names("bootstrap"), [])

    def test_unknown_bootstrap_with_lost_window_never_creates_a_second_chat(self):
        self.zen.bootstrap_mode = "unknown-lost"
        self.create()
        self.zen.bootstrap_mode = "ok"
        retry = self.create()
        self.assertFalse(retry["ok"])
        self.assertEqual(retry["task"]["phase"], "bootstrap-unverified")
        self.assertIn("No task content was sent", retry["error"])
        self.assertEqual(len(self.zen.names("bootstrap")), 1)

    def test_definite_send_failure_retries_once_on_next_request(self):
        self.zen.send_modes = ["not-sent"]
        first = self.create()
        self.assertEqual((first["task"]["phase"], first["task"]["status"]), ("prompt-not-sent", "waiting"))
        retry = self.create()
        self.assertTrue(retry["ok"])
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        keys = [e[4] for e in self.zen.names("send")]
        self.assertEqual(len(set(keys)), len(keys))  # distinct send keys per attempt

    def test_unconfirmed_send_that_landed_is_not_resent(self):
        self.zen.send_modes = ["unknown-delivered"]
        result = self.create()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task"]["phase"], "running")
        self.assertEqual(len(self.zen.names("send")), 1)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        self.assertTrue(any(e[0] == "open" and e[3] is True for e in self.zen.events))

    def test_unconfirmed_send_proven_absent_after_reload_is_sent_once(self):
        self.zen.send_modes = ["unknown-lost"]
        result = self.create()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.zen.names("send")), 2)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_unexpected_user_turn_blocks_instead_of_resending(self):
        self.zen.send_modes = ["unknown-lost"]
        self.zen.worker_send_prompt_orig = self.zen.worker_send_prompt

        def send_then_user_types(task_id, **kw):
            result = self.zen.worker_send_prompt_orig(task_id, **kw)
            self.zen.chats[kw["conversation_id"]]["users"].append("something the user typed")
            return result
        self.zen.worker_send_prompt = send_then_user_types
        result = self.create()
        self.assertFalse(result["ok"])
        self.assertEqual(result["task"]["phase"], "prompt-unconfirmed")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 0)
        self.assertEqual(len(self.zen.names("send")), 1)

    def test_crash_during_send_recovers_from_conversation_without_resend(self):
        # Simulate a process that died after persisting prompt-sending and
        # after the browser delivered the prompt.
        self.zen.worker_send_prompt_orig = self.zen.worker_send_prompt

        def crash(task_id, **kw):
            self.zen.worker_send_prompt_orig(task_id, **kw)
            raise SystemExit("backend killed mid-send")
        self.zen.worker_send_prompt = crash
        with self.assertRaises(SystemExit):
            self.create()
        task = WorkingStore(self.path).find_request("req-1")
        self.assertEqual(task["phase"], "prompt-sending")
        self.zen.worker_send_prompt = lambda *a, **k: self.fail("resent after crash")
        restarted = WebWorkerManager(WorkingStore(self.path), self.zen, project_names=dict(DEFAULT_PROJECT_NAMES),
                                       reconcile_delay=0)
        result = restarted.create(request_id="req-1", title="Build it", prompt=self.PROMPT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_send_attempts_are_bounded(self):
        self.zen.send_modes = ["not-sent"] * (MAX_SEND_ATTEMPTS + 2)
        for _ in range(MAX_SEND_ATTEMPTS + 1):
            last = self.create()
        self.assertFalse(last["ok"])
        self.assertIn("repeated attempts", last["error"])
        self.assertEqual(len(self.zen.names("send")), MAX_SEND_ATTEMPTS)

    def test_custom_working_project_name(self):
        self.zen.projects[2] = project("working", "Loom Working")
        result = self.create(working_project="Loom Working")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task"]["projectName"], "Loom Working")

    def test_old_browser_controller_is_rejected_before_any_chat_is_reserved(self):
        self.zen.web_worker_bridge_ready = lambda: False
        outcome = self.manager.create_background(request_id="stale", title="No", prompt="Never send")
        self.assertFalse(outcome["ok"])
        self.assertIn("not loaded", outcome["error"])
        self.assertIsNone(self.store.find_request("stale"))
        self.assertEqual(self.zen.events, [])

    def test_old_controller_blocks_routing_and_review_without_browser_io(self):
        task = self.create()["task"]
        self.to_review(task)
        events = len(self.zen.events)
        self.zen.web_worker_bridge_ready = lambda: False
        for result in (self.manager.route(task_id=task["id"], lifecycle="vault", reason="x"),
                       self.manager.route_chat(url=task["url"], lifecycle="vault"),
                       self.manager.review(task_id=task["id"], decision="approved", reviewer="r", evidence="e")):
            self.assertFalse(result["ok"])
            self.assertIn("not loaded", result["error"])
        self.assertEqual(len(self.zen.events), events)

    def test_background_create_returns_before_browser_work_finishes(self):
        release = threading.Event()
        original = self.zen.worker_prepare
        self.zen.worker_prepare = lambda tid: (release.wait(2), original(tid))[1]
        completed = threading.Event()
        started = time.monotonic()
        result = self.manager.create_background(request_id="bg", title="Build", prompt=self.PROMPT,
                                                on_complete=completed.set)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual((result["result"], result["task"]["phase"]), ("starting", "reserved"))
        again = self.manager.create_background(request_id="bg", title="Build", prompt=self.PROMPT)
        self.assertEqual(again["result"], "already-running")
        release.set()
        self.assertTrue(completed.wait(2))
        self.assertEqual(self.store.get(result["task"]["id"])["phase"], "running")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_background_resumes_failed_move(self):
        self.zen.move_mode = "fail"
        first = self.create("bg-resume")
        self.zen.move_mode = "ok"
        completed = threading.Event()
        result = self.manager.create_background(request_id="bg-resume", title="Build", prompt=self.PROMPT,
                                                on_complete=completed.set)
        self.assertEqual(result["result"], "resuming")
        self.assertTrue(completed.wait(2))
        self.assertEqual(self.store.get(first["task"]["id"])["phase"], "running")
        self.assertEqual(len(self.zen.chats), 1)


class ObserveAndReviewTests(Base):
    def test_response_waits_for_review_then_verified_done_move(self):
        task = self.create()["task"]
        inspected = self.settle(task)
        self.assertEqual((inspected["status"], inspected["phase"], inspected["lifecycle"]),
                         ("waiting", "awaiting-review", "review"))
        self.assertEqual(chat_route(inspected["url"])["projectId"], project("review")["id"])
        reviewed = self.manager.review(task_id=task["id"], decision="approved", reviewer="review-agent-2",
                                       evidence="tests pass at commit abc")
        self.assertTrue(reviewed["ok"], reviewed)
        done = reviewed["task"]
        self.assertEqual((done["status"], done["projectName"], done["lifecycle"]), ("done", "Done", "done"))
        self.assertEqual(chat_route(done["url"])["projectId"], project("done")["id"])
        self.assertEqual(self.zen.closed, [task["id"]])

    def test_no_review_without_actual_assistant_text(self):
        task = self.create()["task"]
        observed = self.settle(task, text="")
        self.assertEqual(observed["phase"], "running")
        self.assertEqual(self.zen.names("move")[-1][2], project("working")["id"])

    def test_review_move_failure_keeps_task_running_and_backs_off(self):
        task = self.create()["task"]
        self.zen.move_mode = "claim-only"
        observed = self.settle(task)
        self.assertEqual((observed["phase"], observed["lifecycle"]), ("running", "working"))
        self.assertIn("Review", observed["lastError"])
        moves = len(self.zen.names("move"))
        for _ in range(6):   # within the backoff window: no new browser work
            self.manager.inspect(task["id"])
        self.assertEqual(len(self.zen.names("move")), moves)
        self.zen.move_mode = "ok"
        self.manager._review_retry_at.clear()
        self.assertEqual(self.settle(task)["phase"], "awaiting-review")

    def test_wrong_conversation_response_does_not_enter_review(self):
        task = self.create()["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 5
        other = {"ok": True, "working": False, "assistantCount": 9, "assistantText": "someone else",
                 "href": "https://chatgpt.com/c/other-chat"}
        for _ in range(4):
            observed = self.manager.observe(task["id"], other)
        self.assertEqual((observed["phase"], observed["response"]), ("running", ""))

    def test_review_refuses_when_live_output_changed(self):
        task = self.to_review(self.create()["task"])
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 3, "assistantText": "edited later"}
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="r", evidence="ok")
        self.assertFalse(result["ok"])
        self.assertIn("does not match", result["error"])
        self.zen.latest = {"ok": True, "working": True, "assistantCount": 2, "assistantText": "done"}
        self.assertFalse(self.manager.review(task_id=task["id"], decision="approved", reviewer="r",
                                             evidence="ok")["ok"])

    def test_review_requires_verified_review_project(self):
        task = self.create()["task"]
        self.store.update(task["id"], status="waiting", phase="awaiting-review", response="done")
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="r", evidence="ok")
        self.assertFalse(result["ok"])
        self.assertIn("Review project", result["error"])

    def test_reconcile_existing_resumes_interrupted_send_without_resending(self):
        self.zen.worker_send_prompt_orig = self.zen.worker_send_prompt

        def crash(task_id, **kw):
            self.zen.worker_send_prompt_orig(task_id, **kw)
            raise SystemExit("backend killed mid-send")
        self.zen.worker_send_prompt = crash
        with self.assertRaises(SystemExit):
            self.create()
        task = self.store.find_request("req-1")
        self.zen.worker_send_prompt = lambda *a, **k: self.fail("reconcile resent the prompt")
        result = self.manager.reconcile_existing(task["id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task"]["phase"], "running")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)
        again = self.manager.reconcile_existing(task["id"])
        self.assertEqual(again["result"], "not-resumable")

    def test_reconcile_background_returns_before_browser_work(self):
        self.zen.move_mode = "fail"
        task = self.create()["task"]
        self.zen.move_mode = "ok"
        done = threading.Event()
        result = self.manager.reconcile_background(task["id"], on_complete=done.set)
        self.assertEqual(result["result"], "resuming")
        self.assertTrue(done.wait(2))
        self.assertEqual(self.store.get(task["id"])["phase"], "running")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_reviewer_must_be_independent_of_creator(self):
        task = self.to_review(self.create(created_by="Codex-1")["task"])
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="codex-1",
                                     evidence="looks fine")
        self.assertFalse(result["ok"])
        self.assertIn("independent", result["error"])
        self.assertEqual(self.store.get(task["id"])["phase"], "awaiting-review")

    def test_review_requires_evidence(self):
        task = self.to_review(self.create()["task"])
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="r", evidence=" ")
        self.assertFalse(result["ok"])

    def test_unverified_done_move_does_not_mark_done(self):
        task = self.to_review(self.create()["task"])
        self.zen.move_mode = "claim-only"
        result = self.manager.review(task_id=task["id"], decision="approved", reviewer="r", evidence="ok")
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.get(task["id"])["phase"], "awaiting-review")

    def test_rejected_review_is_filed_in_blocked(self):
        task = self.to_review(self.create()["task"])
        result = self.manager.review(task_id=task["id"], decision="rejected", reviewer="r",
                                     evidence="tests fail")
        self.assertTrue(result["ok"])
        self.assertTrue(result["routed"])
        blocked = result["task"]
        self.assertEqual((blocked["status"], blocked["phase"], blocked["lifecycle"]),
                         ("blocked", "review-rejected", "blocked"))
        self.assertEqual(blocked["projectId"], project("blocked")["id"])

    def test_rejection_is_recorded_even_if_blocked_move_fails(self):
        task = self.to_review(self.create()["task"])
        self.zen.move_mode = "fail"
        result = self.manager.review(task_id=task["id"], decision="rejected", reviewer="r", evidence="bad")
        self.assertTrue(result["ok"])
        self.assertFalse(result["routed"])
        self.assertEqual(result["task"]["phase"], "review-rejected")
        self.assertEqual(result["task"]["lifecycle"], "review")  # still where it verifiably is

    def test_rejected_worker_is_terminal_for_idempotent_create(self):
        task = self.to_review(self.create()["task"])
        self.manager.review(task_id=task["id"], decision="rejected", reviewer="r", evidence="unsafe")
        retried = self.create()
        self.assertEqual(retried["result"], "existing")
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)

    def test_running_transition_captures_current_assistant_count(self):
        self.zen.latest = {"ok": True, "working": False, "assistantCount": 4, "assistantText": "older"}
        task = self.create("baseline")["task"]
        self.assertEqual(task["baselineAssistantCount"], 4)
        self.store._tasks[0]["createdAt"] = time.time() - 5
        for _ in range(4):
            observed = self.manager.observe(task["id"], self.zen.worker_latest_response(task["id"]))
        self.assertEqual(observed["phase"], "running")

    def test_transient_idle_does_not_complete_and_review_response_refreshes(self):
        task = self.create("debounce")["task"]
        self.store._tasks[0]["createdAt"] = time.time() - 5
        idle = {"ok": True, "working": False, "assistantCount": 2, "assistantText": "partial",
                "href": self.zen.windows[task["id"]]}
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "running")
        self.manager.observe(task["id"], {**idle, "working": True})
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "running")
        self.manager.observe(task["id"], idle)
        self.assertEqual(self.manager.observe(task["id"], idle)["phase"], "awaiting-review")
        refreshed = self.manager.observe(task["id"], {**idle, "assistantText": "complete response"})
        self.assertEqual(refreshed["response"], "complete response")

    def test_inspect_restores_closed_worker_from_canonical_url(self):
        task = self.create("inspect-restore")["task"]
        self.zen.windows.clear()
        inspected = self.manager.inspect(task["id"])
        self.assertTrue(inspected["live"]["ok"])
        self.assertEqual(self.zen.events[-1], ("open", task["id"], task["url"], False))

    def test_generic_store_done_and_reopen_cannot_bypass_web_worker_review(self):
        task = self.create("generic-done-guard")["task"]
        with self.assertRaisesRegex(ValueError, "loom_web_worker_review"):
            self.store.complete(task["id"])
        with self.assertRaisesRegex(ValueError, "verified review"):
            self.store.update(task["id"], status="done")
        with self.assertRaisesRegex(ValueError, "loom_web_worker_review"):
            self.store.reopen(task["id"])

    def test_generic_backend_tools_reject_web_worker_without_closing_it(self):
        from backend import TabbyBackend
        task = self.create("backend-done-guard")["task"]
        backend = TabbyBackend.__new__(TabbyBackend)
        backend.working = self.store
        backend.voice = self.zen
        backend._working_idle_ticks = {}
        backend._working_retry_at = {}
        backend._publish_working = lambda: None
        attempts = (backend.work_complete(task["id"], "claimed done"),
                    backend.work_update(task["id"], status="done"),
                    backend.work_reopen(task["id"]))
        self.assertTrue(all(not r["ok"] for r in attempts))
        self.assertEqual(self.store.get(task["id"])["phase"], "running")
        self.assertEqual(self.zen.closed, [])


class RoutingTests(Base):
    def test_route_to_blocked_and_vault_requires_reason_and_verifies_id(self):
        task = self.create()["task"]
        self.assertFalse(self.manager.route(task_id=task["id"], lifecycle="blocked")["ok"])
        blocked = self.manager.route(task_id=task["id"], lifecycle="blocked", reason="needs API key")
        self.assertTrue(blocked["ok"], blocked)
        self.assertEqual((blocked["task"]["status"], blocked["task"]["phase"], blocked["task"]["projectId"]),
                         ("blocked", "routed-blocked", project("blocked")["id"]))
        vault = self.manager.route(task_id=task["id"], lifecycle="vault", reason="reference only")
        self.assertTrue(vault["ok"])
        self.assertEqual((vault["task"]["phase"], vault["task"]["lifecycle"]), ("vaulted", "vault"))
        self.assertEqual(self.zen.deliveries(self.PROMPT), 1)  # routing never sends

    def test_route_back_to_working_resumes_monitoring_without_sending(self):
        task = self.create()["task"]
        self.manager.route(task_id=task["id"], lifecycle="blocked", reason="waiting on user")
        sends = len(self.zen.names("send"))
        resumed = self.manager.route(task_id=task["id"], lifecycle="working")
        self.assertTrue(resumed["ok"])
        self.assertEqual((resumed["task"]["status"], resumed["task"]["phase"]), ("working", "running"))
        self.assertEqual(len(self.zen.names("send")), sends)

    def test_done_and_new_are_not_routable_and_creation_must_finish(self):
        task = self.create()["task"]
        for lifecycle in ("done", "new"):
            self.assertFalse(self.manager.route(task_id=task["id"], lifecycle=lifecycle, reason="x")["ok"])
        self.zen.move_mode = "fail"
        pending = self.create("pending")["task"]
        result = self.manager.route(task_id=pending["id"], lifecycle="vault", reason="x")
        self.assertFalse(result["ok"])
        self.assertIn("cannot be routed", result["error"])

    def test_failed_route_keeps_phase(self):
        task = self.create()["task"]
        self.zen.move_mode = "claim-only"
        result = self.manager.route(task_id=task["id"], lifecycle="vault", reason="x")
        self.assertFalse(result["ok"])
        self.assertEqual(self.store.get(task["id"])["phase"], "running")

    def existing_chat(self, core=""):
        self.zen.chats["user-chat"] = {"project": core, "users": ["hello"]}
        return "https://chatgpt.com/c/user-chat"

    def test_route_existing_chat_by_url_in_hidden_window(self):
        url = self.existing_chat()
        result = self.manager.route_chat(url=url, lifecycle="vault")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["projectId"], project("vault")["id"])
        self.assertEqual(self.zen.chats["user-chat"]["project"], project("vault")["id"])
        handle = self.zen.names("move")[0][1]
        self.assertTrue(handle.startswith("route-"))
        self.assertIn(handle, self.zen.closed)
        resolver = self.zen.names("resolve")[0][1]
        self.assertTrue(resolver.startswith("projects-"))
        self.assertIn(resolver, self.zen.resolver_closed)
        self.assertEqual(self.zen.windows, {})

    def test_route_existing_chat_already_in_project_is_idempotent(self):
        url = self.existing_chat(project("blocked")["id"])
        first = self.manager.route_chat(url=url, lifecycle="blocked")
        self.assertTrue(first["ok"])

    def test_route_current_chat_refuses_during_voice_by_default(self):
        self.zen.engine_href = self.existing_chat()
        self.zen.voice_active = True
        refused = self.manager.route_chat(current=True, lifecycle="working")
        self.assertFalse(refused["ok"])
        self.assertIn("Voice is active", refused["error"])
        self.assertEqual(self.zen.names("move"), [])
        allowed = self.manager.route_chat(current=True, lifecycle="working", during_voice=True)
        self.assertTrue(allowed["ok"], allowed)

    def test_route_chat_to_done_needs_review_evidence(self):
        url = self.existing_chat()
        self.assertFalse(self.manager.route_chat(url=url, lifecycle="done")["ok"])
        self.assertTrue(self.manager.route_chat(url=url, lifecycle="done", reviewer="r", evidence="checked")["ok"])

    def test_route_chat_rejects_non_canonical_urls(self):
        for url in ("https://evil.example/c/x", "https://chatgpt.com/", "https://chatgpt.com/c/local-chatgpt:1"):
            self.assertFalse(self.manager.route_chat(url=url, lifecycle="vault")["ok"])
        self.assertEqual(self.zen.events, [])

    def test_route_chat_for_tracked_worker_updates_worker_state(self):
        task = self.create()["task"]
        result = self.manager.route_chat(url=task["url"], lifecycle="vault", evidence="archive")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(self.store.get(task["id"])["phase"], "vaulted")
        refused = self.manager.route_chat(url=self.store.get(task["id"])["url"], lifecycle="done",
                                          reviewer="r", evidence="e")
        self.assertFalse(refused["ok"])


class ChatProjectTests(unittest.TestCase):
    def test_project_core_id_strips_rename_slug(self):
        core = "g-p-" + "0123456789abcdef" * 2
        self.assertEqual(project_core_id(core + "-working"), core)
        self.assertEqual(project_core_id(core.upper().replace("G-P-", "g-p-")), core)
        self.assertEqual(project_core_id("legacy-id"), "legacy-id")

    def test_chat_route_is_strict(self):
        core = "g-p-" + "1" * 32
        self.assertEqual(chat_route(f"https://chatgpt.com/g/{core}-x/c/abc"),
                         {"conversationId": "abc", "projectSegment": f"{core}-x", "projectId": core})
        self.assertEqual(chat_route("https://chatgpt.com/c/abc")["projectId"], "")
        for bad in ("https://evil.example/c/abc", "http://chatgpt.com/c/abc", "https://chatgpt.com/",
                    "https://chatgpt.com/c/local-chatgpt:1", "https://chatgpt.com/g/x/project"):
            self.assertIsNone(chat_route(bad))
        self.assertTrue(canonical_chat_url("https://chatgpt.com/g/working-id/c/abc"))

    def test_resolve_requires_unique_names(self):
        projects = [project("working"), {"id": "g-p-" + "e" * 32, "name": "WORKING "}]
        hit, error = resolve_project(projects, "Working")
        self.assertIsNone(hit)
        self.assertIn("ambiguous", error)
        resolved, errors = resolve_all([project(k) for k in DEFAULT_PROJECT_NAMES], DEFAULT_PROJECT_NAMES)
        self.assertEqual((set(resolved), errors), (set(DEFAULT_PROJECT_NAMES), []))

    def test_project_name_overrides_ignore_bad_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(json.dumps({"projects": {"working": "Loom Working", "done": "", "bogus": "x"}}))
            names = load_project_names(path)
            self.assertEqual(names["working"], "Loom Working")
            self.assertEqual(names["done"], "Done")
            self.assertNotIn("bogus", names)
            path.write_text("{not json")
            self.assertEqual(load_project_names(path), DEFAULT_PROJECT_NAMES)


class BridgeContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_worker_watchdogs_cover_bridge_deadlines_and_fit_client_timeouts(self):
        bridge = (self.ROOT / "bridge/zen/.hey-tabby.uc.js").read_text()
        zen = (self.ROOT / "tabby/zen.py").read_text()
        expected = {  # command: (internal deadline, client timeout)
            "worker-create": (30000, 35),
            "worker-move-project": (11000, 15),
            "worker-prepare": (15000, 19),
            "worker-bootstrap": (44000, 52),
            "worker-send-prompt": (36000, 42),
            "worker-discover-projects": (3500, 5),
            "worker-resolve-projects": (36000, 44),
        }
        stall = bridge[bridge.index("const stalledCommandLimit"):]
        for command, (internal, client) in expected.items():
            watchdog = int(re.search(rf'if \(name === "{command}"\) return (\d+);', bridge).group(1))
            self.assertGreater(watchdog, internal, command)
            self.assertLess(watchdog, client * 1000, command)
            self.assertIn(f"'{command}',timeout={client}", zen.replace(" ", ""), command)
            limit = re.search(rf'if \(name === "{command}"\) return (\d+);', stall)
            self.assertGreater(int(limit.group(1)) if limit else 15000, watchdog,
                               f"{command} would trigger controller takeover mid-command")

    def test_text_sending_commands_are_never_redelivered_by_the_client(self):
        from tabby import zen as zen_module
        client = zen_module.ZenClient.__new__(zen_module.ZenClient)
        with tempfile.TemporaryDirectory() as tmp:
            client.command = Path(tmp) / "cmd.json"
            client._lock = threading.Lock()
            client._last_seq = 0
            client.ensure = lambda: True
            client._read = lambda: {}
            client._running = lambda: True
            client._recycle_engine_window = lambda: None
            writes = []
            original = Path.replace

            def counting_replace(self_path, target):
                writes.append(json.loads(Path(self_path).read_text())["command"])
                return original(self_path, target)
            Path.replace = counting_replace
            try:
                orig_sleep = zen_module.time.sleep
                zen_module.time.sleep = lambda s: None
                for command in ("worker-send-prompt", "worker-bootstrap", "worker-create", "worker-status"):
                    self.assertEqual(client.call(command, timeout=0.01)["result"], "zen-bridge-timeout")
            finally:
                Path.replace = original
                zen_module.time.sleep = orig_sleep
        self.assertEqual(writes.count("worker-send-prompt"), 1)
        self.assertEqual(writes.count("worker-bootstrap"), 1)
        self.assertEqual(writes.count("worker-create"), 1)
        self.assertEqual(writes.count("worker-status"), 2)

    def test_backend_requires_the_move_first_bridge_version(self):
        from tabby.zen import WEB_WORKER_BRIDGE_VERSION, RENAME_BRIDGE_VERSION, bridge_version
        bridge = (self.ROOT / "bridge/zen/.hey-tabby.uc.js").read_text()
        self.assertIn(f'const VERSION = "{RENAME_BRIDGE_VERSION}";', bridge)
        self.assertGreater(bridge_version(RENAME_BRIDGE_VERSION), bridge_version(WEB_WORKER_BRIDGE_VERSION))
        theme = json.loads((self.ROOT / "bridge/zen/theme.json").read_text())
        self.assertEqual(theme["version"], RENAME_BRIDGE_VERSION)

    def test_mcp_exposes_routing_tools_without_claiming_native_project_api(self):
        import loom_mcp
        tools = {name: desc for name, desc, *_ in loom_mcp.TOOLS}
        for name in ("loom_web_worker_create", "loom_web_worker_route", "loom_chat_route", "loom_web_worker_reconcile",
                     "loom_web_worker_review", "loom_web_worker_inspect"):
            self.assertIn(name, tools)
        self.assertIn("no project API", tools["loom_web_worker_create"])
        self.assertIn("verif", tools["loom_chat_route"])


if __name__ == "__main__":
    unittest.main()
