"""Durable ChatGPT web-worker lifecycle; browser details stay in ZenClient."""
from __future__ import annotations

from urllib.parse import urlparse
import threading
import time


def canonical_chat_url(value: str) -> bool:
    u = urlparse(str(value or ""))
    parts = [p for p in u.path.split("/") if p]
    direct = len(parts) == 2 and parts[0] == "c"
    project = len(parts) == 4 and parts[0] == "g" and parts[2] == "c"
    conversation_id = parts[-1] if direct or project else ""
    return (u.scheme == "https" and u.netloc == "chatgpt.com" and bool(conversation_id)
            and not conversation_id.startswith("local-chatgpt"))


def _same_name(left, right) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


class WebWorkerManager:
    def __init__(self, store, zen):
        self.store, self.zen = store, zen
        self._lock = threading.RLock()
        self._inflight = set()
        self._idle_ticks = {}

    def create_background(self, *, request_id, title, prompt, working_project, on_complete=None):
        """Reserve synchronously, then do all browser work off the IPC thread."""
        ready=getattr(self.zen,'web_worker_bridge_ready',None)
        if callable(ready) and not ready():
            return {"ok":False,"error":"Zen's updated Loom web-worker bridge is staged but not yet loaded; reload the Sine Loom mod safely before creating web workers"}
        try:
            task, created = self.store.create_web_worker(request_id, title, prompt)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if (not created and task.get("phase") not in
                {"reserved", "project-not-found", "working-move-failed"}):
            return {"ok": True, "result": "existing", "task": task}
        with self._lock:
            if task["id"] in self._inflight:
                return {"ok": True, "result": "already-sending", "task": task}
            self._inflight.add(task["id"])
        # A process may have stopped after durable reservation but before any
        # browser I/O. That state is the one safe case where recovery should
        # perform the original create/send rather than merely look for a chat.
        launch_as_new = created or task.get("phase") == "reserved"
        if launch_as_new:
            task = self.store.update(task["id"], status="waiting", phase="sending",
                                     summary="Prompt submission started")

        def run():
            try:
                self.create(request_id=request_id, title=title, prompt=prompt,
                            working_project=working_project, _created=launch_as_new)
            finally:
                with self._lock:
                    self._inflight.discard(task["id"])
                if on_complete:
                    on_complete()

        threading.Thread(target=run, name=f"tabby-web-worker-{task['id']}", daemon=True).start()
        return {"ok": True, "result": "sending" if launch_as_new else "resuming", "task": task}

    def create(self, *, request_id, title, prompt, working_project, _created=None):
        try:
            task, created_now = self.store.create_web_worker(request_id, title, prompt)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        created = created_now if _created is None else bool(_created)
        if (not created and task.get("phase") not in
                {"reserved", "project-not-found", "working-move-failed"}):
            return {"ok": True, "result": "existing", "task": task}
        if (not created and canonical_chat_url(task.get("url", ""))
                and task.get("phase") in {"project-not-found", "working-move-failed"}):
            opened = self.zen.worker_open(task["id"], task["url"], reload=False)
            if not opened.get("ok"):
                launched = {**opened, "href": task["url"]}
            else:
                launched = self.zen.worker_discover_projects(task["id"])
                launched = {**launched, "href": task["url"]}
        else:
            # This durable boundary is deliberately before browser I/O: after it
            # is persisted, no retry may send the prompt again.
            task = self.store.update(task["id"], status="waiting", phase="sending",
                                     summary="Prompt submission started")
            launched = self.zen.worker_create(task["id"], prompt)
        url = str(launched.get("href") or "")
        if not canonical_chat_url(url):
            # A browser navigation can complete just after the controller's
            # result. Inspect the exact existing worker rather than resending.
            # This is particularly important after a timeout or interrupted
            # command where the prompt may already have been delivered.
            recovered = self.zen.worker_recover(task["id"])
            if recovered.get("ok") and canonical_chat_url(recovered.get("href", "")):
                launched = recovered
                url = str(recovered["href"])
        if not launched.get("ok") or not canonical_chat_url(url):
            task = self.store.update(task["id"], status="blocked", phase="creation-failed",
                                     summary=str(launched.get("result") or "canonical conversation was not verified"))
            return {"ok": False, "error": "canonical conversation was not verified", "task": task}
        projects = launched.get("projects") or []
        match = next((p for p in projects if _same_name(p.get("name"), working_project)), None)
        if not match:
            task = self.store.update(task["id"], url=url, status="blocked", phase="project-not-found",
                                     summary=f"ChatGPT project not found: {working_project}")
            return {"ok": False, "error": "working project not found", "projects": projects, "task": task}
        moved = self.zen.worker_move_project(task["id"], str(match.get("id") or ""), working_project)
        if (not moved.get("ok") or not _same_name(moved.get("projectName"), working_project)
                or str(moved.get("projectId") or "") != str(match.get("id") or "")):
            task = self.store.update(task["id"], url=url, status="blocked", phase="working-move-failed",
                                     summary="Working project move could not be verified")
            return {"ok": False, "error": "working project move could not be verified", "task": task}
        live = self.zen.worker_latest_response(task["id"])
        if not live.get("ok"):
            task = self.store.update(task["id"], url=url, status="blocked", phase="working-move-failed",
                                     summary="Assistant response baseline could not be captured")
            return {"ok": False, "error": "assistant response baseline could not be captured", "task": task}
        task = self.store.update(task["id"], url=url, kind="web-worker", status="working", phase="running",
                                 projectId=str(match.get("id") or ""), projectName=working_project,
                                 baselineAssistantCount=int(live.get("assistantCount") or 0))
        return {"ok": True, "result": "created", "task": task}

    def reconcile_existing(self, task_id):
        """Recover an uncertain send without ever sending another prompt."""
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        if canonical_chat_url(task.get("url", "")):
            return {"ok": True, "result": "already-known", "task": task}
        recovered = self.zen.worker_recover(task_id)
        url = str(recovered.get("href") or "")
        if not recovered.get("ok") or not canonical_chat_url(url):
            return {"ok": False, "result": str(recovered.get("result") or "recovery-unconfirmed"),
                    "task": task}
        projects = recovered.get("projects") or []
        task = self.store.update(task_id, url=url, status="blocked", phase="project-not-found",
                                 summary="Worker conversation recovered; Working project not yet verified")
        return {"ok": True, "result": "conversation-recovered", "task": task,
                "projects": projects}

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

    def review(self, *, task_id, decision, reviewer, evidence, done_project):
        task = self.store.get(task_id)
        if not task or task.get("kind") != "web-worker":
            return {"ok": False, "error": "unknown web worker"}
        if task.get("phase") != "awaiting-review":
            return {"ok": False, "error": "worker is not awaiting review"}
        decision = str(decision or "").lower()
        if decision not in {"approved", "rejected"} or not str(reviewer or "").strip() or not str(evidence or "").strip():
            return {"ok": False, "error": "independent reviewer, decision, and evidence are required"}
        if decision == "rejected":
            task = self.store.update(task_id, status="blocked", phase="review-rejected",
                                     reviewDecision=decision, reviewer=reviewer, reviewEvidence=evidence)
            return {"ok": True, "result": "rejected", "task": task}
        discovered = self.zen.worker_discover_projects(task_id)
        if not discovered.get("ok") and canonical_chat_url(task.get("url", "")):
            opened = self.zen.worker_open(task_id, task["url"], reload=False)
            if not opened.get("ok"):
                return {"ok": False, "error": "worker window could not be restored", "task": task}
            discovered = self.zen.worker_discover_projects(task_id)
        if not discovered.get("ok"):
            return {"ok": False, "error": "Done project discovery failed", "task": task}
        match = next((p for p in (discovered.get("projects") or [])
                      if _same_name(p.get("name"), done_project)), None)
        if not match:
            return {"ok": False, "error": "Done project not found", "task": task}
        moved = self.zen.worker_move_project(task_id, str(match.get("id") or ""), done_project)
        if (not moved.get("ok") or not _same_name(moved.get("projectName"), done_project)
                or str(moved.get("projectId") or "") != str(match.get("id") or "")):
            return {"ok": False, "error": "Done project move could not be verified", "task": task}
        task = self.store.complete_web_worker_review(
            task_id, projectId=str(match.get("id") or ""), projectName=done_project,
            reviewDecision=decision, reviewer=reviewer, reviewEvidence=evidence)
        self.zen.worker_close(task_id)
        return {"ok": True, "result": "approved", "task": task}
