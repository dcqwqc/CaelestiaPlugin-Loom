"""Durable ChatGPT web-worker lifecycle; browser details stay in ZenClient.

A worker is created move-first:

  reserved -> bootstrapped (blank chat born in the New project)
           -> working-verified (moved; Working project id verified)
           -> running (the real prompt sent exactly once)
           -> awaiting-review -> done (independent review, moved to Done)

Every phase is persisted before the browser I/O it guards, so a crash or retry
resumes from the last verified step instead of starting over. Phases whose
browser outcome is unknown ("...-unverified", "prompt-unconfirmed") are only
resolved by reading the conversation itself, never by blindly repeating.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid

from tabby.chat_projects import (LIFECYCLES, chat_route, load_project_names,
                                 project_core_id, resolve_project)


def canonical_chat_url(value: str) -> bool:
    return chat_route(value) is not None


def _norm(text) -> str:
    return " ".join(str(text or "").split())


def _prompt_matches(prompt, user_text) -> bool:
    want, seen = _norm(prompt)[:200], _norm(user_text)
    return bool(want) and (want in seen or seen[:len(want)] == want)


BOOTSTRAP_TEXT = ("Loom worker setup {task_id}. This chat is being filed into its project. "
                  "Do not start any work yet: the task arrives in the next message. Reply only: READY")

# Phases from which create() may (re)drive work, keyed by the step they need.
_BOOTSTRAP = {"reserved", "prepare-failed", "project-not-found", "bootstrap-failed"}
_MOVE = {"bootstrapped", "working-move-failed"}
_SEND = {"working-verified", "prompt-not-sent"}
_RECONCILE_BOOTSTRAP = {"bootstrapping", "bootstrap-unverified"}
_RECONCILE_SEND = {"prompt-sending", "prompt-unconfirmed"}
RESUMABLE = _BOOTSTRAP | _MOVE | _SEND | _RECONCILE_BOOTSTRAP | _RECONCILE_SEND | {"moving"}
MAX_SEND_ATTEMPTS = 3
# Lifecycle targets loom_web_worker_route may use. "new" is creation-only and
# "done" requires loom_web_worker_review.
ROUTABLE = {"working", "blocked", "vault"}


class WebWorkerManager:
    def __init__(self, store, zen, project_names=None, reconcile_delay=6.0):
        self.store, self.zen = store, zen
        self.reconcile_delay = reconcile_delay
        self._names = project_names
        self._lock = threading.RLock()
        self._inflight = set()
        self._idle_ticks = {}

    def names(self, **overrides):
        names = dict(self._names) if self._names else load_project_names()
        for lifecycle, name in overrides.items():
            if lifecycle in names and str(name or "").strip():
                names[lifecycle] = str(name).strip()
        return names

    # ----------------------------------------------------------------- create

    def _bridge_error(self):
        ready = getattr(self.zen, 'web_worker_bridge_ready', None)
        if callable(ready) and not ready():
            return {"ok": False, "error": "Zen's move-first Loom web-worker bridge is not loaded yet; "
                                          "reload the Sine Loom mod when no Voice session is active"}
        return None

    def create_background(self, *, request_id, title, prompt, working_project=None,
                          created_by="", on_complete=None):
        """Reserve synchronously, then do all browser work off the IPC thread."""
        if (blocked := self._bridge_error()):
            return blocked
        try:
            task, created = self.store.create_web_worker(request_id, title, prompt, created_by)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not created and task.get("phase") not in RESUMABLE:
            return {"ok": True, "result": "existing", "task": task}
        with self._lock:
            if task["id"] in self._inflight:
                return {"ok": True, "result": "already-running", "task": task}
            self._inflight.add(task["id"])

        def run():
            try:
                self.create(request_id=request_id, title=title, prompt=prompt,
                            working_project=working_project, created_by=created_by)
            finally:
                with self._lock:
                    self._inflight.discard(task["id"])
                if on_complete:
                    on_complete()

        threading.Thread(target=run, name=f"tabby-web-worker-{task['id']}", daemon=True).start()
        return {"ok": True, "result": "starting" if created else "resuming", "task": task}

    def create(self, *, request_id, title, prompt, working_project=None, created_by=""):
        try:
            task, _ = self.store.create_web_worker(request_id, title, prompt, created_by)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if task.get("phase") not in RESUMABLE:
            return {"ok": True, "result": "existing", "task": task}
        names = self.names(working=working_project)
        for _ in range(8):  # each pass advances one persisted step
            phase = task.get("phase")
            if phase in _BOOTSTRAP:
                task, error = self._bootstrap(task, names)
            elif phase in _RECONCILE_BOOTSTRAP:
                task, error = self._reconcile_bootstrap(task)
            elif phase in _MOVE or phase == "moving":
                task, error = self._move_to_working(task, names)
            elif phase in _SEND:
                task, error = self._send_prompt(task, names)
            elif phase in _RECONCILE_SEND:
                task, error = self._reconcile_send(task, names)
            else:
                break
            if error:
                return {"ok": False, "error": error, "task": task}
        if task.get("phase") == "running":
            return {"ok": True, "result": "created", "task": task}
        return {"ok": False, "error": f"worker stopped in phase {task.get('phase')}", "task": task}

    def _fail(self, task, phase, error, *, status="blocked", **values):
        task = self.store.update(task["id"], status=status, phase=phase, lastError=error,
                                 summary=error, **values)
        return task, error

    def _resolve(self, window_id, lifecycles, names):
        """Learn exact project ids by opening each project in a blank window.

        Returns ({lifecycle: project}, [errors]). The window must not show a
        chat: resolution navigates it.
        """
        wanted = [names[l] for l in lifecycles]
        found = self.zen.worker_resolve_projects(window_id, wanted)
        if not found.get("ok"):
            return {}, ["ChatGPT projects could not be resolved: " + str(found.get("result") or "unknown")]
        failures = {str(e.get("name") or "").casefold(): str(e.get("result") or "") for e in found.get("errors") or []}
        resolved, errors = {}, []
        for lifecycle in lifecycles:
            name = names[lifecycle]
            hit, error = resolve_project(found.get("projects"), name)
            if hit:
                resolved[lifecycle] = hit
            else:
                why = failures.get(name.casefold())
                errors.append(f"{error} ({why})" if why and "ambiguous" not in error else error)
        return resolved, errors

    def _resolve_aside(self, lifecycles, names):
        """Resolve in a throwaway window so a chat window is never navigated."""
        handle = "projects-" + uuid.uuid4().hex[:10]
        try:
            prep = self.zen.worker_prepare(handle)
            if not prep.get("ok"):
                return {}, ["project resolver window could not be opened: " + str(prep.get("result") or "unknown")]
            return self._resolve(handle, lifecycles, names)
        finally:
            self.zen.worker_close(handle)

    def _bootstrap(self, task, names):
        # Opening the blank composer and listing projects never sends anything.
        prep = self.zen.worker_prepare(task["id"])
        if not prep.get("ok"):
            return self._fail(task, "prepare-failed", "Worker window could not be prepared: "
                              + str(prep.get("result") or "unknown"))
        existing = chat_route(prep.get("href"))
        if existing:
            # The window is keyed to this task, so a chat already in it is this
            # worker's bootstrap from an interrupted run: adopt, never recreate.
            return self._adopt_bootstrap(task, prep["href"], existing), ""
        # The task window is still blank, so it can be used to resolve ids.
        projects, errors = self._resolve(task["id"], LIFECYCLES, names)
        if errors:
            return self._fail(task, "project-not-found", "; ".join(errors))
        new = projects["new"]
        task = self.store.update(task["id"], status="waiting", phase="bootstrapping", lastError="",
                                 summary="Creating a blank chat in the New project")
        boot = self.zen.worker_bootstrap(task["id"], new.get("segment") or new["id"], new["id"],
                                         BOOTSTRAP_TEXT.format(task_id=task["id"]))
        route = chat_route(boot.get("href"))
        if boot.get("ok") and route and route["projectId"] == new["id"]:
            return self._adopt_bootstrap(task, boot["href"], route, new), ""
        if boot.get("sent") is False:
            return self._fail(task, "bootstrap-failed", "Blank chat was not created: "
                              + str(boot.get("result") or "unknown"))
        return self._fail(task, "bootstrap-unverified",
                          "Blank chat creation could not be verified (" + str(boot.get("result") or "unknown")
                          + "). No task content was sent; Loom will not create a second chat for this request.")

    def _adopt_bootstrap(self, task, href, route, new_project=None):
        # Only a route proven to be in the New project is labelled as such; an
        # adopted chat of unknown placement is simply moved to Working next.
        in_new = bool(new_project and route["projectId"] == new_project["id"])
        return self.store.update(task["id"], url=href, status="waiting", phase="bootstrapped",
                                 projectId=route["projectId"], lifecycle="new" if in_new else "",
                                 projectName=new_project["name"] if in_new else "", lastError="",
                                 summary="Blank chat created; moving to Working before sending the task")

    def _reconcile_bootstrap(self, task):
        prep = self.zen.worker_prepare(task["id"])
        route = chat_route(prep.get("href")) if prep.get("ok") else None
        if route:
            return self._adopt_bootstrap(task, prep["href"], route), ""
        return self._fail(task, "bootstrap-unverified",
                          "Bootstrap outcome is unknown and its window is gone. No task content was sent; "
                          "use a new request_id if the work is still needed.")

    def _ensure_window(self, task):
        """Make sure the worker window shows this task's conversation."""
        want = chat_route(task.get("url"))
        if not want:
            return None
        status = self.zen.worker_status(task["id"])
        route = chat_route(status.get("href")) if status.get("ok") else None
        if route and route["conversationId"] == want["conversationId"]:
            return route
        opened = self.zen.worker_open(task["id"], task["url"], reload=False)
        if not opened.get("ok"):
            return None
        status = self.zen.worker_status(task["id"])
        route = chat_route(status.get("href")) if status.get("ok") else None
        return route if route and route["conversationId"] == want["conversationId"] else None

    def _move_verified(self, task_id, conversation_id, project):
        """Move and verify via two independent reads; returns (href, error)."""
        moved = self.zen.worker_move_project(task_id, project["id"], project["name"], conversation_id)
        claimed = (moved.get("ok") and project_core_id(moved.get("projectId")) == project["id"]
                   and str(moved.get("conversationId") or conversation_id) == conversation_id)
        if not claimed:
            return "", "project move could not be verified: " + str(moved.get("result") or "unknown")
        status = self.zen.worker_status(task_id)
        route = chat_route(status.get("href")) if status.get("ok") else None
        if not route or route["conversationId"] != conversation_id or route["projectId"] != project["id"]:
            return "", "conversation route does not show the requested project id after the move"
        return status["href"], ""

    def _move_to_working(self, task, names):
        resolved, errors = self._resolve_aside(["working"], names)
        working = resolved.get("working")
        if not working:
            return self._fail(task, "working-move-failed", "; ".join(errors) or "Working project not resolved")
        route = self._ensure_window(task)
        if not route:
            return self._fail(task, "working-move-failed", "Worker conversation could not be reopened")
        task = self.store.update(task["id"], phase="moving", summary="Moving to Working")
        href, error = self._move_verified(task["id"], route["conversationId"], working)
        if error:
            return self._fail(task, "working-move-failed", "Working " + error)
        turns = self.zen.worker_turns(task["id"])
        if not turns.get("ok") or int(turns.get("userCount") or 0) < 1:
            return self._fail(task, "working-move-failed", "Conversation turns could not be read after the move",
                              url=href)
        task = self.store.update(task["id"], url=href, status="waiting", phase="working-verified",
                                 projectId=working["id"], projectName=working["name"], lifecycle="working",
                                 expectedUserCount=int(turns["userCount"]), lastError="",
                                 summary="In Working (project id verified); sending the task")
        return task, ""

    def _send_prompt(self, task, names):
        attempt = int(task.get("sendAttempts") or 0) + 1
        if attempt > MAX_SEND_ATTEMPTS:
            return self._fail(task, "prompt-not-sent", "Task prompt was not sent after repeated attempts")
        route = self._ensure_window(task)
        if not route or route["projectId"] != task.get("projectId"):
            return self._fail(task, "working-move-failed", "Conversation is no longer in the verified Working project")
        live = self.zen.worker_latest_response(task["id"])
        baseline = int(live.get("assistantCount") or 0) if live.get("ok") else int(task.get("expectedUserCount") or 0)
        # Persisted before the click: from here on, recovery reads the chat.
        task = self.store.update(task["id"], phase="prompt-sending", sendAttempts=attempt,
                                 baselineAssistantCount=baseline, summary="Sending task prompt")
        sent = self.zen.worker_send_prompt(
            task["id"], conversation_id=route["conversationId"], project_id=task["projectId"],
            prompt=task["prompt"], send_key=f"{task['id']}:{attempt}",
            expected_user_count=int(task.get("expectedUserCount") or 0))
        if sent.get("ok") and sent.get("sent") is True:
            return self._running(task, baseline), ""
        if sent.get("sent") is False and sent.get("result") != "user-count-mismatch":
            return self._fail(task, "prompt-not-sent", "Task prompt was not sent: " + str(sent.get("result")),
                              status="waiting")
        # Unknown outcome: record it, then let the caller reconcile from the
        # conversation itself (never by sending again blind).
        task, _ = self._fail(task, "prompt-unconfirmed", "Task prompt delivery is unconfirmed: "
                             + str(sent.get("result") or "unknown"))
        return task, ""

    def _running(self, task, baseline):
        return self.store.update(task["id"], status="working", phase="running",
                                 baselineAssistantCount=baseline, lastError="",
                                 summary="Task sent in the verified Working project")

    def _reconcile_send(self, task, names):
        """Decide from the conversation itself whether the prompt landed."""
        if not canonical_chat_url(task.get("url")):
            return self._fail(task, "prompt-unconfirmed", "Worker has no verified conversation URL")
        # Give an in-flight submission time to reach ChatGPT before the reload.
        if self.reconcile_delay:
            time.sleep(self.reconcile_delay)
        opened = self.zen.worker_open(task["id"], task["url"], reload=True)
        turns = self.zen.worker_turns(task["id"]) if opened.get("ok") else opened
        if not turns.get("ok"):
            return self._fail(task, "prompt-unconfirmed", "Conversation could not be read to confirm delivery")
        expected = int(task.get("expectedUserCount") or 0)
        count = int(turns.get("userCount") or 0)
        if count > expected and _prompt_matches(task.get("prompt"), turns.get("lastUserText")):
            return self._running(task, max(expected, int(task.get("baselineAssistantCount") or 0))), ""
        if count == expected and expected >= 1:
            # The reloaded chat proves the prompt is absent: safe to send.
            return self.store.update(task["id"], phase="prompt-not-sent", status="waiting",
                                     summary="Prompt confirmed absent; retrying send"), ""
        return self._fail(task, "prompt-unconfirmed",
                          "Conversation has an unexpected user turn; refusing to send again")

    # ---------------------------------------------------------------- observe

    def inspect(self, task_id):
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        live = self.zen.worker_latest_response(task_id) if task.get("url") else {}
        if task.get("url") and not live.get("ok"):
            opened = self.zen.worker_open(task_id, task["url"], reload=False)
            live = self.zen.worker_latest_response(task_id) if opened.get("ok") else opened
        task = self.observe(task_id, live) or task
        return {"ok": True, "task": task, "live": live}

    def observe(self, task_id, live):
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker" or not live.get("ok"):
            return task
        phase = task.get("phase")
        count = int(live.get("assistantCount") or 0)
        response = str(live.get("assistantText") or "")[:12000]
        if phase == "awaiting-review" and count > int(task.get("baselineAssistantCount") or 0):
            if response and response != task.get("response"):
                return self.store.update(task_id, response=response)
            return task
        if phase != "running":
            return task
        if live.get("working"):
            with self._lock:
                self._idle_ticks[task_id] = 0
            return task
        evidence = bool(task.get("sawWorking")) or count > int(task.get("baselineAssistantCount") or 0)
        age = time.time() - float(task.get("createdAt") or time.time())
        with self._lock:
            ticks = int(self._idle_ticks.get(task_id, 0)) + 1 if evidence and age >= 4.0 else 0
            self._idle_ticks[task_id] = ticks
            if ticks >= 3:
                self._idle_ticks.pop(task_id, None)
                return self.store.update(task_id, status="waiting", phase="awaiting-review", response=response)
        return task

    # ---------------------------------------------------------------- routing

    def _route_task(self, task, lifecycle, names):
        """Move a worker's chat to a lifecycle project; returns (href, project, error)."""
        resolved, errors = self._resolve_aside([lifecycle], names)
        project = resolved.get(lifecycle)
        if not project:
            return "", None, "; ".join(errors) or "project not resolved"
        route = self._ensure_window(task)
        if not route:
            return "", None, "worker conversation could not be reopened"
        href, error = self._move_verified(task["id"], route["conversationId"], project)
        return href, project, error

    def review(self, *, task_id, decision, reviewer, evidence, done_project=None, blocked_project=None):
        if (blocked := self._bridge_error()):
            return blocked
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        if task.get("phase") != "awaiting-review":
            return {"ok": False, "error": "worker is not awaiting review"}
        decision = str(decision or "").lower()
        reviewer = str(reviewer or "").strip()
        if decision not in {"approved", "rejected"} or not reviewer or not str(evidence or "").strip():
            return {"ok": False, "error": "independent reviewer, decision, and evidence are required"}
        if task.get("createdBy") and reviewer.casefold() == str(task["createdBy"]).strip().casefold():
            return {"ok": False, "error": "reviewer must be independent of the agent that created the worker"}
        names = self.names(done=done_project, blocked=blocked_project)
        if decision == "rejected":
            href, project, error = self._route_task(task, "blocked", names)
            values = dict(status="blocked", phase="review-rejected", reviewDecision=decision,
                          reviewer=reviewer, reviewEvidence=evidence)
            if error:
                # The rejection is recorded truthfully even if filing fails.
                task = self.store.update(task_id, lastError="Blocked " + error,
                                         summary="Review rejected; Blocked project move not verified", **values)
                return {"ok": True, "result": "rejected", "routed": False, "error": "Blocked " + error, "task": task}
            task = self.store.update(task_id, url=href, projectId=project["id"], projectName=project["name"],
                                     lifecycle="blocked", lastError="", summary="Review rejected; filed in Blocked",
                                     **values)
            return {"ok": True, "result": "rejected", "routed": True, "task": task}
        href, project, error = self._route_task(task, "done", names)
        if error:
            return {"ok": False, "error": "Done " + error, "task": task}
        task = self.store.complete_web_worker_review(
            task_id, projectId=project["id"], projectName=project["name"],
            reviewDecision=decision, reviewer=reviewer, reviewEvidence=evidence)
        task = self.store.update(task_id, url=href, lifecycle="done") or task
        self.zen.worker_close(task_id)
        return {"ok": True, "result": "approved", "task": task}

    def route(self, *, task_id, lifecycle, reason="", project_name=None):
        """File a running/reviewed worker into Working, Blocked or Vault.

        Never sends anything to the conversation. Done needs loom_web_worker_review;
        New is only used at creation.
        """
        if (blocked := self._bridge_error()):
            return blocked
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        lifecycle = str(lifecycle or "").lower()
        if lifecycle not in ROUTABLE:
            return {"ok": False, "error": "lifecycle must be working, blocked or vault (done requires review)"}
        if task.get("phase") not in {"running", "awaiting-review", "review-rejected", "routed-blocked", "vaulted"}:
            return {"ok": False, "error": f"worker in phase {task.get('phase')} cannot be routed; "
                                          "finish or recover creation first", "task": task}
        if lifecycle in {"blocked", "vault"} and not str(reason or "").strip():
            return {"ok": False, "error": "a reason is required for blocked or vault routing"}
        href, project, error = self._route_task(task, lifecycle, self.names(**{lifecycle: project_name}))
        if error:
            task = self.store.update(task_id, lastError=f"{lifecycle.title()} {error}")
            return {"ok": False, "error": f"{lifecycle.title()} {error}", "task": task}
        values = dict(url=href, projectId=project["id"], projectName=project["name"], lifecycle=lifecycle,
                      routeReason=str(reason or "")[:1000], lastError="")
        if lifecycle == "working":
            live = self.zen.worker_latest_response(task_id)
            values.update(status="working", phase="running", sawWorking=False,
                          baselineAssistantCount=int(live.get("assistantCount") or 0) if live.get("ok") else 0,
                          summary="Back in Working (project id verified)")
        elif lifecycle == "blocked":
            values.update(status="blocked", phase="routed-blocked", summary=f"Blocked: {reason}")
        else:
            values.update(status="waiting", phase="vaulted", summary=f"Vaulted: {reason}")
        task = self.store.update(task_id, **values)
        if lifecycle != "working":
            self.zen.worker_close(task_id)
        return {"ok": True, "result": f"routed-{lifecycle}", "task": task}

    def route_chat(self, *, url="", lifecycle, current=False, reviewer="", evidence="",
                   during_voice=False, project_name=None):
        """File an existing (non-worker) ChatGPT conversation by URL or Loom's current chat.

        Uses a separate hidden worker window, never the Voice engine window.
        """
        if (blocked := self._bridge_error()):
            return blocked
        lifecycle = str(lifecycle or "").lower()
        if lifecycle not in LIFECYCLES:
            return {"ok": False, "error": f"lifecycle must be one of {', '.join(LIFECYCLES)}"}
        if lifecycle == "done" and not (str(reviewer or "").strip() and str(evidence or "").strip()):
            return {"ok": False, "error": "routing to Done requires an independent reviewer and evidence"}
        if current:
            status = self.zen.status()
            if not status.get("ok"):
                return {"ok": False, "error": "Loom's current chat could not be read"}
            if status.get("active") and not during_voice:
                return {"ok": False, "error": "Voice is active in the current chat; route it after Voice ends "
                                              "(or pass during_voice=true to accept the risk)"}
            url = str(status.get("href") or "")
        want = chat_route(url)
        if not want:
            return {"ok": False, "error": "a canonical https://chatgpt.com conversation URL is required"}
        tracked = next((t for t in self.store.list() if t.get("kind") == "web-worker"
                        and (chat_route(t.get("url")) or {}).get("conversationId") == want["conversationId"]), None)
        if tracked:
            if lifecycle == "done":
                return {"ok": False, "error": "this chat is a tracked web worker; use loom_web_worker_review",
                        "task_id": tracked["id"]}
            return {**self.route(task_id=tracked["id"], lifecycle=lifecycle, reason=evidence or "routed by URL",
                                 project_name=project_name), "task_id": tracked["id"]}
        names = self.names(**{lifecycle: project_name})
        resolved, errors = self._resolve_aside([lifecycle], names)
        project = resolved.get(lifecycle)
        if not project:
            return {"ok": False, "error": "; ".join(errors) or "project not resolved"}
        handle = "route-" + hashlib.sha1(want["conversationId"].encode()).hexdigest()[:12]
        try:
            opened = self.zen.worker_open(handle, url, reload=False)
            if not opened.get("ok"):
                return {"ok": False, "error": "conversation could not be opened in a worker window"}
            href, error = self._move_verified(handle, want["conversationId"], project)
            if error:
                return {"ok": False, "error": f"{lifecycle.title()} {error}"}
            return {"ok": True, "result": f"routed-{lifecycle}", "url": href, "lifecycle": lifecycle,
                    "projectId": project["id"], "projectName": project["name"],
                    "conversationId": want["conversationId"]}
        finally:
            self.zen.worker_close(handle)
